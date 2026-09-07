import sys, json, sqlite3
sys.path.insert(0, '.')
from data.leagues import LEAGUE_DATA

# --- 1. leagues.py 기준 진실 데이터 구축 ---
country_index = {}  # country_name -> {"max_tier":.., "total":.., "teams": {(name,tier)}, "names": set(all names)}
for cname, tiers in LEAGUE_DATA.items():
    names_by_tier = set()
    all_names = set()
    total = 0
    for tier, (lname, teams) in tiers.items():
        total += len(teams)
        for t in teams:
            names_by_tier.add((t, tier))
            all_names.add(t)
    country_index[cname] = {
        "max_tier": max(tiers.keys()),
        "total": total,
        "team_tier_pairs": names_by_tier,
        "all_names": all_names,
    }

# id 매핑 (game.db 없을 수 있으니 이름 기준으로만 우선 검증, id는 참고용)
try:
    conn = sqlite3.connect('game.db')
    c = conn.cursor()
    c.execute("SELECT id, name FROM countries")
    name_to_id = {name: cid for cid, name in c.fetchall()}
except Exception:
    name_to_id = {}

# --- 2. jsonl 로드 & 검증 (빈 줄로 구분된 pretty JSON 블록 파싱, country_id는 무시) ---
raw = open(os.path.join(_path.ROOT, 'data', 'affiliate_raw.jsonl'), encoding='utf-8').read()
blocks = [b for b in raw.split('\n\n') if b.strip()]
lines = blocks

errors = []
warnings = []
seen_country_names = []
ok_count = 0

for i, line in enumerate(lines, 1):
    try:
        rec = json.loads(line)
    except Exception as e:
        errors.append(f"[줄 {i}] JSON 파싱 실패: {e}")
        continue

    cname = rec.get("country_name")
    cid = rec.get("country_id")

    if cname not in country_index:
        errors.append(f"[줄 {i}] country_name '{cname}' 이(가) leagues.py에 없음")
        continue

    seen_country_names.append(cname)
    # country_id는 더 이상 검증하지 않음 (폐기, country_name만 신뢰)

    info = country_index[cname]

    # total_teams 체크
    if rec.get("total_teams") != info["total"]:
        errors.append(f"[줄 {i}][{cname}] total_teams 불일치: 파일={rec.get('total_teams')}, 실제={info['total']}")

    # affiliates / review 항목 검증
    for group in ("affiliates", "review"):
        for item in rec.get(group, []):
            tname = item.get("team_name")
            tier = item.get("tier")
            if (tname, tier) not in info["team_tier_pairs"]:
                errors.append(f"[줄 {i}][{cname}] {group}: '{tname}'(tier {tier}) 이(가) leagues.py에 없음")
            if group == "affiliates":
                pname = item.get("parent_team_name")
                if pname == tname:
                    errors.append(f"[줄 {i}][{cname}] 자기참조: '{tname}' -> parent가 자기 자신")
                elif pname not in info["all_names"]:
                    errors.append(f"[줄 {i}][{cname}] parent_team_name '{pname}' 이(가) leagues.py에 없음 (team: {tname})")

    if not any(e.startswith(f"[줄 {i}]") for e in errors):
        ok_count += 1

# --- 3. 커버리지 체크 ---
all_countries = set(LEAGUE_DATA.keys())
covered = set(seen_country_names)
missing = all_countries - covered
dup = [c for c in set(seen_country_names) if seen_country_names.count(c) > 1]

print(f"총 국가 수(leagues.py): {len(all_countries)}")
print(f"jsonl에 기록된 줄 수: {len(lines)}")
print(f"정상 처리(오류 없음) 국가 수: {ok_count}")
print(f"중복 국가: {dup if dup else '없음'}")
print(f"누락 국가 수: {len(missing)}")
if missing:
    print("누락 국가 목록:", sorted(missing))

print(f"\n=== 오류 {len(errors)}건 ===")
for e in errors:
    print(e)

print(f"\n=== 경고 {len(warnings)}건 ===")
for w in warnings:
    print(w)