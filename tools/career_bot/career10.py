import json, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # 프로젝트 루트(constants)
from constants import OFFER_ZONES
B = sys.argv[1] if len(sys.argv) > 1 else "."   # 사용: python3 career10.py <런 결과 폴더들이 있는 폴더>
def zone(w):
    for s, e in OFFER_ZONES:
        if s <= w <= e: return s
for arm in ("base", "new"):
    for w in (1, 2):
        L = json.load(open(f"{B}/s10_{arm}_w{w}/bot_log.json"))
        Z = defaultdict(int); dec = []
        for x in L["windows"]:
            if x.get("fa"): continue
            Z[(x["year"], zone(x["week"]))] = max(Z[(x["year"], zone(x["week"]))], x["n"])
            if x["n"]: dec.append(x)
        zero = sum(1 for v in Z.values() if v == 0)
        path = " → ".join(f"{m['year']}/{m['week']}주 " + (f"{m['new']['name']}({m['new']['country']} {m['new']['tier']}부)" if m.get('new') else "무소속") for m in L["moves"])
        seas = [(s["year"], s["age"], s["ovr"], (s["team"] or {}).get("name"), (s["team"] or {}).get("lgrade"), (s["team"] or {}).get("tier"), s.get("military")) for s in L["seasons"]]
        acc = sum(1 for x in dec if x.get("decision") == "accept"); ref = sum(1 for x in dec if x.get("refused"))
        print(f"== {arm} 시드{w}: 오퍼 구간 {len(Z)}개 중 오퍼 0개 {zero} ({zero/len(Z):.0%}) | 수락 {acc} 구단거절 {ref} | 재계약 {len(L['renewals'])} | 정원초과 주 {len(L['cap_violations'])} | 무결성 {L.get('integrity_passed')} | {L.get('elapsed')}s")
        print("   이적:", path or "없음")
        print("   시즌:", seas)
        print("   떠난 팀 보충:", [((m.get('old') or {}).get('name'), (m.get('old') or {}).get('n'), (m.get('old_after') or {}).get('n')) for m in L['moves']])
