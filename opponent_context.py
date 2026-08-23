# -*- coding: utf-8 -*-
"""
opponent_context.py — [2026-08 신설, PHASE 2: 상대 재평가, 신민용+GPT 협업 설계]

"당시엔 그냥 한 경기였는데, 그 상대가 나중에 그 대회에서 우승/준우승/4강까지
갔다"는 서사를 만드는 독립 모듈. story_generator.py 전용이 아니라 나중에
경기 상세 화면/구단 역사/국가대표 역사 등 다른 곳에서도 재사용할 수 있도록
game_engine이나 story_generator에 종속시키지 않고 따로 뺐다(GPT 설계 권고).

설계 원칙(신민용+GPT 합의):
- "상대가 강했다/약했다"는 게임 내부 수치(OVR 등)로 판정하지 않는다. 오직
  "그 대회에서 실제로 어디까지 갔는가"(intl_tournaments.winner + intl_matches
  스테이지 기록)만 본다 — 이게 데이터로 증명 가능한 유일한 사후 평가다.
- v1은 국가대표 국제대회(월드컵/대륙컵/지역컵)만 다룬다. 클럽 대항전(CL 등)
  확장은 나중에(같은 원리로 cl_tournaments/cl_matches를 보면 되므로 구조는
  그대로 재사용 가능하지만, 이번엔 범위를 좁게 유지).
- 문장은 FACT + INTERPRETATION만 만든다("그는 그 경기를 평생 잊지 못했다"
  같은 MOTIVE/EMOTION 문장은 만들지 않는다) — 원본 스코어/결과는 절대
  바꾸지 않고, 그 위에 "대회가 끝난 뒤 확정된 상대의 최종 성적"이라는
  사실 하나만 얹는다.
"""

from database import get_conn

# intl_engine.STAGE_KO의 원본 stage 코드와 동일 — "얼마나 멀리 갔는가"를
# 판정할 진행 단계만 순위로 둔다(TP=3/4위전은 진행 단계가 아니라 별도
# 이벤트라 여기서 제외 — SF에서 진 팀이 TP에 나가는 것뿐이라 SF 도달로
# 이미 충분히 잡힌다).
_STAGE_PROGRESS_RANK = {
    "group": 0, "qual_group": 0, "qual_po": 0,
    "R32": 1, "R16": 2, "QF": 3, "SF": 4, "F": 5,
}


def get_intl_opponent_stage(tournament_id, country):
    """국가대표 국제대회 하나(tournament_id)에서 country가 최종적으로
    도달한 단계를 판정한다.
    반환: "CHAMPION" | "FINALIST" | "SEMIFINALIST" | None
    (None = 8강 이하에서 탈락했거나 참가 기록을 찾을 수 없음 — 이 경우
    서사적 가치가 낮다고 보고 태그를 안 붙인다)."""
    if not tournament_id or not country:
        return None
    conn = get_conn()
    try:
        t = conn.execute(
            "SELECT winner FROM intl_tournaments WHERE id=?",
            (tournament_id,)).fetchone()
        if t and t["winner"] == country:
            return "CHAMPION"

        rows = conn.execute(
            """SELECT stage FROM intl_matches
               WHERE tournament_id=? AND (home=? OR away=?) AND home_score>=0""",
            (tournament_id, country, country)).fetchall()
    finally:
        conn.close()

    if not rows:
        return None
    best_stage, best_rank = None, -1
    for r in rows:
        rank = _STAGE_PROGRESS_RANK.get(r["stage"], -1)
        if rank > best_rank:
            best_rank, best_stage = rank, r["stage"]

    if best_stage == "F":
        return "FINALIST"      # 위에서 이미 CHAMPION은 걸러졌으니 여기 오면 준우승
    if best_stage == "SF":
        return "SEMIFINALIST"
    return None


# FACT + INTERPRETATION만 담은 템플릿. {opp}=상대국, {year}=경기 연도 —
# 둘 다 이미 확보된 원본 데이터 그대로 삽입한다. 감정을 단정하는 문장
# ("잊지 못했다" 등)은 넣지 않는다.
_OPPONENT_CONTEXT_TEMPLATES = {
    ("승", "CHAMPION"): [
        "{year}년 {opp}과의 경기는 당시엔 한 번의 승리였지만, {opp}이 그 대회에서 끝내 우승을 차지하면서 결과의 의미가 새롭게 읽히게 됐다.",
        "당시엔 그저 한 경기의 승리였으나, {opp}은 이후 그 대회 정상에 올랐다.",
    ],
    ("무", "CHAMPION"): [
        "{year}년 {opp}과 비긴 경기는 평범한 무승부처럼 보였지만, {opp}이 이후 대회 우승을 차지하면서 다르게 볼 여지가 생겼다.",
    ],
    ("패", "CHAMPION"): [
        "{year}년 {opp}에 패했던 경기는 아쉬움으로 남았지만, {opp}은 그 대회에서 끝내 우승까지 차지했다.",
        "당시엔 그저 한 번의 패배였지만, 대회가 끝난 뒤 {opp}은 그 대회의 우승팀이 되어 있었다.",
    ],
    ("승", "FINALIST"): [
        "{year}년 {opp}을 상대로 거둔 승리는, {opp}이 이후 결승까지 올라갔다는 점에서 다시 눈여겨볼 만하다.",
    ],
    ("무", "FINALIST"): [
        "{year}년 {opp}과의 무승부는, {opp}이 그 대회 결승까지 올라갔다는 사실이 알려진 뒤 다르게 읽히게 됐다.",
    ],
    ("패", "FINALIST"): [
        "{year}년 {opp}에 패한 경기였지만, {opp}은 그 대회에서 결승까지 진출했다.",
    ],
    ("승", "SEMIFINALIST"): [
        "{year}년 꺾었던 {opp}은 그 대회에서 4강까지 오른 팀이었다.",
    ],
    ("무", "SEMIFINALIST"): [
        "{year}년 {opp}과 맞선 경기는 무승부로 끝났는데, {opp}은 그 대회 4강까지 올라간 팀이었다.",
    ],
    ("패", "SEMIFINALIST"): [
        "{year}년 {opp}에 패했지만, {opp}은 그 대회에서 4강까지 진출했다.",
    ],
}


def build_opponent_context_events(intl_matches):
    """get_my_intl_matches()/get_my_qual_matches() 결과를 받아, 상대의
    사후 성적이 확인되는 경기만 골라 (연도, 상대, 내 결과, 상대 최종
    단계) 이벤트 리스트로 만든다. 나머지(태그 없음)는 그냥 버린다 —
    모든 경기를 다 다루면 스토리가 산만해지므로, 서사 가치가 확인된
    경기만 남긴다."""
    events = []
    for m in (intl_matches or []):
        tid = m.get("tournament_id")
        opp = m.get("opp")
        if not tid or not opp:
            continue
        # PSO 표기("승(PSO)" 등)는 승부 결과 자체는 같으므로 앞 글자만 사용.
        result = (m.get("result") or "")[:1]
        if result not in ("승", "무", "패"):
            continue
        stage = get_intl_opponent_stage(tid, opp)
        if not stage:
            continue
        events.append({
            "year": m.get("year"), "opp": opp, "result": result, "stage": stage,
        })
    return events


def build_opponent_context_sentences(rng, intl_matches, limit=2):
    """최종 문장 리스트(최대 limit개)를 만든다. 여러 후보가 있으면
    "상대가 우승한" 경기를 우선하고(서사 가치가 가장 큼), 그 다음
    연도순으로 채운다 — 매 커리어마다 다 다르게 나오도록 rng로 템플릿만
    고른다(사실 자체는 고정)."""
    events = build_opponent_context_events(intl_matches)
    if not events:
        return []
    _stage_priority = {"CHAMPION": 0, "FINALIST": 1, "SEMIFINALIST": 2}
    events.sort(key=lambda e: (_stage_priority.get(e["stage"], 9), e["year"]))

    out = []
    for ev in events[:limit]:
        bank = _OPPONENT_CONTEXT_TEMPLATES.get((ev["result"], ev["stage"]))
        if not bank:
            continue
        out.append(rng.choice(bank).format(year=ev["year"], opp=ev["opp"]))
    return out