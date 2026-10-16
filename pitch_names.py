"""포메이션(피치) 그림 위 선수 이름 축약 표기.

[2026-10 신설, 신민용 확정] 피치 원 아래 이름만 줄인다 — 옆 명단·목록·복사
텍스트는 원래 이름 그대로. 적용 화면: 메인 포메이션(_FormationCanvas), 세계
기록실 팀/국가/세계 베스트11 피치(_StaticPitchView), 경기 상세 라인업 평점 피치.

규칙(우선순위 순):
  1. 그대로 두는 이름 — 빈 값/"(공석)"류, AI 코드(AI0JMR·"AI 1"), 이미 점이 든
     이름("H. 콜린스" — 사용자가 직접 줄여 지은 것), 띄어쓰기 없는 한 단어
     (한국식 붙여 쓴 이름, 브라질식 한 단어 등록명 "카이우" 등).
  2. 한국어권(대한민국·북한)·베트남어·몽골어 국적 → 풀네임.
     베트남은 성이 응우옌·쩐·레에 몰려 성 기준 표기가 다 겹치고, 몽골은 앞이
     성이 아니라 아버지 이름이라 "이름 이니셜. 성" 틀이 맞지 않는다.
  3. 성이 앞인 언어권(일본어·중국어·광둥어·크메르어) → 뒤(이름) 첫 글자 + 앞(성).
     구보 다케후사 → T. 구보
  4. 나머지(이름이 앞) → 첫 단어(이름) 첫 글자 + 마지막 단어(성).
     티아구 몬테이루 → T. 몬테이루. 성 앞의 반·판·폰·데·드·디·다·델·델라·도스·두
     같은 연결어는 성에 붙인다(버질 반 다이크 → V. 반 다이크 — 아니면 "V. 다이크").
  5. 한 피치(한 팀) 안에서 줄인 이름이 겹치면 겹친 선수만 풀네임.

영문 이니셜은 한글 첫 음절의 초성(ㅇ이면 모음)으로 추정한다 — 가상 이름이라
원어 철자를 복원할 필요는 없고 일관성이 우선(신민용 확정: "예외를 무리하게
늘리지 않기"). 국적 예외는 한글 표기 관습 때문에 체계적으로 틀어지는 것만:
  · 스페인어권 ㅎ → J (후안·호세·하비에르 — 스페인어 J를 한글로 ㅎ로 적음)
  · 포르투갈어권 ㅎ → R (호나우두·호드리구·후벵 — 포르투갈어 첫 R을 ㅎ로 적음)
  · 일본어 ㄱ → K, ㄷ → T (가오루·다케후사 — 일본어 첫 K/T를 ㄱ/ㄷ으로 적음)
ㅋ은 C로 통일(카이우 → C., 케빈도 C.).

Qt 없이 import 되는 모듈(헤드리스 테스트용) — Qt가 필요한 draw_fitted_text만
함수 안에서 import한다."""
import re
from collections import Counter

# ── 한글 → 영문 이니셜 ───────────────────────────────────────────
#   초성 19개: ㄱ ㄲ ㄴ ㄷ ㄸ ㄹ ㅁ ㅂ ㅃ ㅅ ㅆ ㅇ ㅈ ㅉ ㅊ ㅋ ㅌ ㅍ ㅎ
_CHO_LETTER = ["G", "K", "N", "D", "T", "L", "M", "B", "P", "S", "S", None,
               "J", "J", "C", "C", "T", "P", "H"]
#   ㅇ(소리 없음)일 때 중성 21개로 정한다:
#   ㅏ ㅐ ㅑ ㅒ ㅓ ㅔ ㅕ ㅖ ㅗ ㅘ ㅙ ㅚ ㅛ ㅜ ㅝ ㅞ ㅟ ㅠ ㅡ ㅢ ㅣ
_JUNG_LETTER = ["A", "A", "Y", "Y", "E", "E", "Y", "Y", "O", "W", "W", "O", "Y",
                "U", "W", "W", "W", "Y", "E", "U", "I"]
_CHO_IEUNG, _CHO_GIYEOK, _CHO_DIGEUT, _CHO_HIEUT = 11, 0, 3, 18

# 국적 언어(data/countries.py COUNTRY_DATA 4번째 값) 기준 분류
_FULL_NAME_LANGS = {"한국어", "베트남어", "몽골어"}
_FAMILY_FIRST_LANGS = {"일본어", "중국어", "광둥어", "크메르어"}
_LANG_CHO_OVERRIDE = {
    "스페인어": {_CHO_HIEUT: "J"},
    "포르투갈어": {_CHO_HIEUT: "R"},
    "일본어": {_CHO_GIYEOK: "K", _CHO_DIGEUT: "T"},
}
# 성 앞에 붙는 연결어(네덜란드·독일·이베리아·이탈리아·프랑스식)
_SURNAME_PARTICLES = {"반", "판", "폰", "데", "더", "데르", "드", "디", "다", "델", "델라", "도스", "두",
                      "다스", "덴", "테르", "반데르", "판데르", "엘", "알"}
_AI_CODE_RE = re.compile(r"^AI(?:[0-9A-Z]{4}| ?\d+)$")

_LANG_BY_COUNTRY = None


def _lang_of(nationality):
    global _LANG_BY_COUNTRY
    if _LANG_BY_COUNTRY is None:
        try:
            from data.countries import COUNTRY_DATA
            _LANG_BY_COUNTRY = {r[0]: r[3] for r in COUNTRY_DATA}
        except Exception:
            _LANG_BY_COUNTRY = {}
    return _LANG_BY_COUNTRY.get((nationality or "").strip(), "")


def name_initial(word, lang=""):
    """단어(이름) 첫 글자 → 영문 대문자 이니셜. 영문으로 지은 이름은 그 글자 그대로."""
    if not word:
        return "?"
    ch = word[0]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        idx = code - 0xAC00
        cho, jung = idx // 588, (idx % 588) // 28
        over = _LANG_CHO_OVERRIDE.get(lang, {})
        if cho in over:
            return over[cho]
        if cho == _CHO_IEUNG:
            return _JUNG_LETTER[jung]
        return _CHO_LETTER[cho]
    return ch.upper()


def short_name(name, nationality=""):
    """피치 표시용 이름 하나. 규칙은 모듈 설명 참고."""
    s = (name or "").strip()
    if not s or s.startswith("(") or "." in s or _AI_CODE_RE.match(s):
        return s
    toks = s.split()
    if len(toks) < 2:
        return s
    lang = _lang_of(nationality)
    if lang in _FULL_NAME_LANGS:
        return s
    if lang in _FAMILY_FIRST_LANGS:
        return f"{name_initial(toks[1], lang)}. {toks[0]}"
    start = len(toks) - 1
    while start > 1 and toks[start - 1] in _SURNAME_PARTICLES:
        start -= 1
    return f"{name_initial(toks[0], lang)}. {' '.join(toks[start:])}"


def short_names(items):
    """items: [(이름, 국적) 또는 None, ...] — 한 피치(한 팀) 단위.
    줄인 이름이 서로 겹치면 겹친 선수만 원래 이름으로 되돌린다."""
    outs = [short_name(it[0], it[1]) if it else None for it in items]
    cnt = Counter(o for o in outs if o)
    for i, it in enumerate(items):
        if it and outs[i] and cnt[outs[i]] > 1 and outs[i] != (it[0] or "").strip():
            outs[i] = (it[0] or "").strip()
    return outs


def lookup_nationalities(ids):
    """선수 id → 국적. AI(0 이상)는 현역→은퇴 아카이브 순, -1은 내 선수.
    id<-1(국제대회 가상 폴백 등)·못 찾은 id는 빠진다(→ 기본 규칙)."""
    want = {i for i in ids if isinstance(i, int)}
    if not want:
        return {}
    from database import get_conn
    out = {}
    ai = sorted(i for i in want if i >= 0)
    conn = get_conn()
    try:
        for tbl in ("ai_players", "ai_players_retired"):
            rest = [i for i in ai if i not in out]
            for k in range(0, len(rest), 500):
                chunk = rest[k:k + 500]
                try:
                    rows = conn.execute(
                        f"SELECT id, nationality FROM {tbl} WHERE id IN ({','.join('?' * len(chunk))})",
                        chunk).fetchall()
                except Exception:
                    rows = []
                for r in rows:
                    out[r[0]] = r[1] or ""
        if -1 in want:
            try:
                r = conn.execute("SELECT nationality FROM my_player WHERE id=1").fetchone()
                if r:
                    out[-1] = r[0] or ""
            except Exception:
                pass
    finally:
        conn.close()
    return out


class NationalityCache:
    """위젯 하나가 들고 있는 국적 캐시 — paintEvent마다 DB를 열지 않게 같은 id
    묶음이면 재사용한다. 새 게임에서 id가 재사용돼도 위젯의 선수 묶음이 바뀌면
    키가 달라져 다시 조회된다."""

    def __init__(self):
        self._key = None
        self._map = {}

    def get(self, ids):
        key = tuple(sorted({i for i in ids if isinstance(i, int)}))
        if key != self._key:
            try:
                self._map = lookup_nationalities(key)
            except Exception as e:
                print(f"[PITCH-NAME] 국적 조회 실패(기본 규칙으로 표시): {e}")
                self._map = {}
            self._key = key
        return self._map


def row_name_widths(points, default_w, min_w, max_w=110, gap=6):
    """points: [(x, y), ...] → 각 원 아래 이름에 쓸 수 있는 폭. 같은 줄(같은 y)
    이웃과의 가장 가까운 가로 간격만큼까지 넓히고(겹침 방지), 이웃이 없으면 max_w."""
    out = []
    for i, (x, y) in enumerate(points):
        dists = [abs(x - x2) for j, (x2, y2) in enumerate(points)
                 if j != i and abs(y - y2) <= 2]
        room = (min(dists) - gap) if dists else max_w
        out.append(int(max(min_w, min(max_w, max(default_w, room)))))
    return out


def draw_fitted_text(painter, x, y, w, h, text, align, base_pt, min_pt=6, bold=False):
    """글자 수로 자르지 않고 실제 폭에 맞춘다 — 넘치면 글씨를 min_pt까지 줄이고,
    그래도 넘칠 때만 말줄임(…). 예전 [:2]/[:6]/[:8] 잘림(CAM→CA, 맥레오드→맥레오) 대체."""
    from PyQt6.QtGui import QFont, QFontMetrics
    from PyQt6.QtCore import Qt
    text = text or ""
    f = QFont()
    f.setBold(bold)
    pt = max(min_pt, int(base_pt))
    while True:
        f.setPointSize(pt)
        fm = QFontMetrics(f)
        if fm.horizontalAdvance(text) <= w or pt <= min_pt:
            break
        pt -= 1
    painter.setFont(f)
    if fm.horizontalAdvance(text) > w:
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w))
    painter.drawText(int(x), int(y), int(w), int(h), align, text)