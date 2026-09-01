# -*- coding: utf-8 -*-
"""match_sim/motion_metrics.py — 22명 움직임의 "품질"을 실축 단위로 계량한다.

## 왜 이게 먼저인가

지금까지 움직임 개선은 전부 "보니까 이상하다 → elif 하나 추가"였다.
match_sim_viewer.py의 주석을 보면 같은 증상("몇 명만 반응하고 나머지는
얼어있다", "무지성으로 왔다갔다", "흐물흐물")이 서로 다른 함수에서 반복해서
지적되고 반복해서 "근본 원인"이라며 수정된다. 이건 고칠 때마다 **좋아졌는지
확인할 수 없었기 때문**이다. 눈으로 보는 것만으론 회귀를 못 잡는다.

이 모듈은 궤적 로그(프레임 리스트)를 받아서 정규화 좌표를 **미터/초 단위로
환산**하고, 실제 축구 트래킹 데이터의 공개된 참조값과 나란히 놓는다.
그래야 "허접하다"가 "선수당 이동거리가 실축의 18%다" 같은 수치가 된다.

## 진단 축 (무엇을 재는가)

기존 headless_motion_probe.py는 **결함**(순간이동/고정/겹침)만 봤다. 즉
"버그가 없는가"만 봤지 "축구처럼 보이는가"는 안 봤다. 그래서 버그가 0건인
채로도 여전히 허접할 수 있었다. 이 모듈은 반대쪽을 본다:

  A. 활동량   — 이동거리, 속도 분포, 스프린트 횟수, 정지 시간 비율
  B. 대형     — 팀 길이/폭, 컴팩트니스, 라인 간격
  C. 볼 관계  — 볼 주변 압박 밀도, 팀 중심과 볼의 동조율
  D. 자율성   — **홈 좌표 이탈도(leash)** 와 **동시 이동 인원 수**

D가 이 프로젝트의 핵심 진단축이다:
  * `home_leash_m` — 선수가 자기 포메이션 기준점에서 평균 몇 m 떨어져
    있는가. 현재 구조는 모든 목표가 `hx + adv` 형태라 이 값이 구조적으로
    작을 수밖에 없다. 실축에서는 한 선수가 경기 중 자기 "포지션"에서
    20~30m씩 예사로 벗어난다. 이 수치가 낮으면 = 고무줄.
  * `movers_per_frame` — 한 프레임에서 유의미하게 움직이는 선수가 몇
    명인가. 역할이 단일 인덱스(_presser_idx 등)로만 할당되는 현재 구조에선
    22명 중 소수만 나온다. 이 수치가 낮으면 = "나머지는 얼어있다".

## 사용법

    from match_sim.motion_metrics import analyze_frames, format_report

    rep = analyze_frames(frames, home_pos, away_pos, home_home_xy, away_home_xy)
    print(format_report(rep))

`frames`는 sim_engine._snapshot_frame() 형식의 dict 리스트:
    {"clock": float(분), "home": [(x,y)*11], "away": [(x,y)*11],
     "ball": (x,y), ...}

## 좌표/시간 환산

정규화 좌표 x∈[0,1]이 피치 길이, y∈[0,1]이 피치 폭이다. 표준 피치
105m × 68m 기준으로 환산한다. 시간은 프레임 간 clock 차이(분)를 초로
바꿔서 쓴다 — 뷰어의 내부 적분 dt(0.12)가 아니라 **경기 시계 기준**으로
재는 게 중요하다. 그래야 "90분 동안 몇 km 뛰었나"가 실축과 비교 가능한
숫자가 된다. (이 둘이 어긋나 있으면 그 사실 자체가 진단 결과다.)
"""

import math
import statistics as _st

# ── 피치 규격 (FIFA 표준) ──
PITCH_LEN_M = 105.0
PITCH_WID_M = 68.0

# ── 판정 임계값 ──
SPRINT_MS = 7.0        # 이 속도 이상이면 스프린트 1회로 계수(실축 통용 기준)
STATIONARY_MS = 0.5    # 이 속도 미만이면 사실상 정지
MOVER_MS = 1.5         # "지금 유의미하게 움직이는 중"으로 볼 최소 속도
PRESS_RADIUS_M = 5.0   # 볼 주변 압박 밀도를 세는 반경
PRESS_RADIUS2_M = 10.0

_GK = "GK"
_DEF = {"CB", "LB", "RB", "LWB", "RWB"}
_MID = {"CDM", "CM", "CAM", "LM", "RM"}
_FWD = {"ST", "CF", "LW", "RW"}


# ══════════════════════════════════════════════════════════════════
# 실축 참조값 — 공개된 트래킹 데이터(EPL/UCL 계열) 기반의 대략적 범위.
# 정확한 단일 수치가 아니라 "이 구간 밖이면 명백히 이상하다"는 판정용
# 밴드다. 튜닝 목표는 이 밴드 안에 들어가는 것이지 중앙값을 맞히는 게
# 아니다.
# ══════════════════════════════════════════════════════════════════
REFERENCE = {
    "dist_per_outfielder_km":  (9.0, 12.0,  "선수당 총 이동거리"),
    "dist_gk_km":              (4.0, 6.0,   "GK 이동거리"),
    "speed_mean_ms":           (1.3, 2.2,   "평균 속도(정지 포함)"),
    "speed_p95_ms":            (4.5, 7.0,   "상위 5% 속도"),
    "speed_max_ms":            (7.5, 10.0,  "최고 속도"),
    "sprints_per_outfielder":  (10.0, 40.0, "선수당 스프린트 횟수"),
    "stationary_frac":         (0.05, 0.35, "정지 상태 시간 비율"),
    "team_length_m":           (28.0, 48.0, "팀 길이(최후방~최전방)"),
    "team_width_m":            (28.0, 52.0, "팀 폭"),
    "compactness_m":           (13.0, 22.0, "중심으로부터 평균거리"),
    "def_fwd_gap_m":           (22.0, 42.0, "수비라인~공격라인 간격"),
    "press_within_5m":         (0.8, 2.5,   "볼 5m 내 선수 수(양팀)"),
    "press_within_10m":        (2.5, 6.0,   "볼 10m 내 선수 수(양팀)"),
    "home_leash_mean_m":       (8.0, 18.0,  "포메이션 기준점 이탈(평균)"),
    "home_leash_p95_m":        (22.0, 45.0, "포메이션 기준점 이탈(상위 5%)"),
    "movers_per_frame":        (8.0, 18.0,  "동시에 움직이는 선수 수(22명 중)"),
    "centroid_ball_corr":      (0.55, 0.90, "팀 중심 x ↔ 볼 x 상관"),
    "lateral_share":           (0.30, 0.55, "총 이동 중 좌우 성분 비율"),
}


def _to_m(dx, dy):
    """정규화 좌표 차이 → 미터."""
    return math.hypot(dx * PITCH_LEN_M, dy * PITCH_WID_M)


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def _pct(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _group(pos):
    if pos == _GK:
        return "GK"
    if pos in _DEF:
        return "DEF"
    if pos in _MID:
        return "MID"
    return "FWD"


def _snap_transitions(frames):
    """[중요] 이 엔진은 코너 크라우드/PK 배치/파울 수비벽/하프타임 재배치를
    **의도적으로 순간이동**시킨다("애들 이동하는 시간 빼려고 넣은 것").
    이 프레임 전이를 속도/이동거리 계산에 포함하면 최고속도가 80 m/s로
    찍히고 스프린트 횟수가 부풀려져서, 정작 재려는 오픈플레이 움직임의
    품질이 가려진다. 그래서 계량에서 제외한다.

    [판정 기준] 배너 텍스트로 판정하면 안 된다 — `banner_text`는 알파가
    0이 된 뒤에도 프레임에 계속 남아 있어서, 코너킥이 한 번 나오면 그
    이후 경기 전체가 "스냅 구간"으로 잘못 잡힌다(실측: 이동거리가
    6.9km → 1.25km로 떨어지는 버그). 엔진이 재개 시점마다 갱신하는
    `last_restart_clock`이 **이번 전이에서 바뀌었는지**만 본다.

    반환: 제외할 전이 인덱스 집합 (k는 frames[k-1] → frames[k] 전이)
    """
    bad = set()
    for k in range(1, len(frames)):
        a, b = frames[k - 1], frames[k]
        if b.get("last_restart_clock", -99.0) != a.get("last_restart_clock", -99.0):
            # 스냅 그 프레임뿐 아니라 직후 2프레임도 뺀다 — 락에서 풀린
            # 선수들이 원래 자리로 복귀하는 구간이라 오픈플레이 움직임이
            # 아니다(실측: 이 구간에서만 10~13 m/s가 찍힘).
            bad.update((k, k + 1, k + 2))
    return bad & set(range(1, len(frames)))


def analyze_frames(frames, home_pos, away_pos,
                   home_home_xy=None, away_home_xy=None,
                   frame_minutes=None):
    """궤적 로그 하나(=경기 1개)를 분석해 지표 dict를 돌려준다.

    frames        : _snapshot_frame() 형식 dict 리스트
    home_pos      : 홈팀 11명의 포지션 라벨 리스트 (인덱스 대응)
    away_pos      : 원정팀 11명의 포지션 라벨 리스트
    home_home_xy  : 홈팀 11명의 포메이션 기준 좌표 [(hx,hy)*11] — leash 계산용.
                    None이면 leash 지표는 생략된다.
    frame_minutes : 프레임 간 경기시간 간격(분). None이면 clock 차이에서 추정.
    """
    if len(frames) < 3:
        raise ValueError("프레임이 너무 적다")

    # ── 프레임 간 실제 경기시간(초) ──
    if frame_minutes is None:
        deltas = [frames[k]["clock"] - frames[k - 1]["clock"] for k in range(1, len(frames))]
        deltas = [d for d in deltas if d > 1e-9]
        frame_minutes = _st.median(deltas) if deltas else 0.06
    dt_s = frame_minutes * 60.0
    n = len(frames)
    total_min = frames[-1]["clock"] - frames[0]["clock"]

    teams = (("home", home_pos, home_home_xy), ("away", away_pos, away_home_xy))
    snaps = _snap_transitions(frames)
    live = [k for k in range(1, n) if k not in snaps]

    out = {
        "n_frames": n,
        "n_snap_transitions": len(snaps),
        "frame_dt_min": frame_minutes,
        "frame_dt_s": dt_s,
        "match_minutes": total_min,
        "per_player": [],
        "teams": {},
    }

    # ══════════════════════════════════════════════════════
    # A. 선수 단위 — 이동거리 / 속도 / 스프린트 / 정지 / leash
    # ══════════════════════════════════════════════════════
    all_speeds = []
    for side, poslist, homexy in teams:
        for i in range(len(poslist)):
            xs = [f[side][i][0] for f in frames]
            ys = [f[side][i][1] for f in frames]
            steps_m = [_to_m(xs[k] - xs[k - 1], ys[k] - ys[k - 1]) for k in live]
            speeds = [s / dt_s for s in steps_m]
            all_speeds.extend(speeds)
            sp_sorted = sorted(speeds)

            # 스프린트: 임계 이상 구간의 "진입 횟수"를 센다(연속 프레임을
            # 한 번으로 묶음) — 실축 통계도 구간 단위로 센다.
            sprints, inside = 0, False
            for s in speeds:
                if s >= SPRINT_MS and not inside:
                    sprints += 1
                    inside = True
                elif s < SPRINT_MS:
                    inside = False

            # 좌우 성분 비율 — 앞뒤로만 왕복하는지, 실제로 옆으로도
            # 움직이는지. 현재 구조는 x가 adv(전진폭)에 지배되므로
            # 이 값이 낮게 나올 것으로 예상된다.
            lat = sum(abs(ys[k] - ys[k - 1]) * PITCH_WID_M for k in live)
            lon = sum(abs(xs[k] - xs[k - 1]) * PITCH_LEN_M for k in live)

            rec = {
                "side": side, "idx": i, "pos": poslist[i], "group": _group(poslist[i]),
                "dist_km": sum(steps_m) / 1000.0,
                "speed_mean": (sum(speeds) / len(speeds)) if speeds else 0.0,
                "speed_p50": _pct(sp_sorted, 0.50),
                "speed_p95": _pct(sp_sorted, 0.95),
                "speed_max": sp_sorted[-1] if sp_sorted else 0.0,
                "sprints": sprints,
                "stationary_frac": (sum(1 for s in speeds if s < STATIONARY_MS) / len(speeds)) if speeds else 1.0,
                "lateral_share": lat / (lat + lon) if (lat + lon) > 0 else 0.0,
            }
            if homexy is not None and i < len(homexy):
                hx, hy = homexy[i]
                leash = sorted(_to_m(xs[k] - hx, ys[k] - hy) for k in range(n))
                rec["leash_mean"] = sum(leash) / len(leash)
                rec["leash_p95"] = _pct(leash, 0.95)
                rec["leash_max"] = leash[-1]
            out["per_player"].append(rec)

    # ══════════════════════════════════════════════════════
    # B. 팀 단위 — 대형(길이/폭/컴팩트니스/라인 간격)
    # ══════════════════════════════════════════════════════
    for side, poslist, _hx in teams:
        outfield = [i for i, p in enumerate(poslist) if p != _GK]
        d_idx = [i for i, p in enumerate(poslist) if p in _DEF]
        f_idx = [i for i, p in enumerate(poslist) if p in _FWD]

        lengths, widths, compacts, gaps, cx_series = [], [], [], [], []
        for f in frames:
            pts = [f[side][i] for i in outfield]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            lengths.append((max(xs) - min(xs)) * PITCH_LEN_M)
            widths.append((max(ys) - min(ys)) * PITCH_WID_M)
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            cx_series.append(cx)
            compacts.append(sum(_to_m(p[0] - cx, p[1] - cy) for p in pts) / len(pts))
            if d_idx and f_idx:
                dx = sum(f[side][i][0] for i in d_idx) / len(d_idx)
                fx = sum(f[side][i][0] for i in f_idx) / len(f_idx)
                gaps.append(abs(fx - dx) * PITCH_LEN_M)

        ball_x = [f["ball"][0] for f in frames]
        out["teams"][side] = {
            "team_length_m": sum(lengths) / len(lengths),
            "team_length_sd": _st.pstdev(lengths),
            "team_width_m": sum(widths) / len(widths),
            "team_width_sd": _st.pstdev(widths),
            "compactness_m": sum(compacts) / len(compacts),
            "def_fwd_gap_m": (sum(gaps) / len(gaps)) if gaps else float("nan"),
            "centroid_ball_corr": _pearson(cx_series, ball_x),
        }

    # ══════════════════════════════════════════════════════
    # C. 볼 관계 — 압박 밀도 / 볼 속도
    # ══════════════════════════════════════════════════════
    p5, p10, ball_speeds = [], [], []
    for k, f in enumerate(frames):
        bx, by = f["ball"]
        c5 = c10 = 0
        for side in ("home", "away"):
            for (x, y) in f[side]:
                d = _to_m(x - bx, y - by)
                if d <= PRESS_RADIUS_M:
                    c5 += 1
                if d <= PRESS_RADIUS2_M:
                    c10 += 1
        p5.append(c5)
        p10.append(c10)
        if k in snaps or k == 0:
            continue
        pbx, pby = frames[k - 1]["ball"]
        ball_speeds.append(_to_m(bx - pbx, by - pby) / dt_s)
    bs_sorted = sorted(ball_speeds)
    out["ball"] = {
        "press_within_5m": sum(p5) / len(p5),
        "press_within_10m": sum(p10) / len(p10),
        "ball_speed_mean": sum(ball_speeds) / len(ball_speeds),
        "ball_speed_p95": _pct(bs_sorted, 0.95),
        "ball_speed_max": bs_sorted[-1] if bs_sorted else 0.0,
    }

    # ══════════════════════════════════════════════════════
    # D. 자율성 — 동시에 움직이는 인원 수
    #   현재 구조의 핵심 결함("22명 중 5명만 행동")을 직접 계량한다.
    # ══════════════════════════════════════════════════════
    movers = []
    for k in live:
        c = 0
        for side in ("home", "away"):
            for i in range(len(frames[k][side])):
                x0, y0 = frames[k - 1][side][i]
                x1, y1 = frames[k][side][i]
                if _to_m(x1 - x0, y1 - y0) / dt_s >= MOVER_MS:
                    c += 1
        movers.append(c)
    mv_sorted = sorted(movers)
    out["movers"] = {
        "movers_per_frame": sum(movers) / len(movers),
        "movers_p50": _pct(mv_sorted, 0.50),
        "movers_p95": _pct(mv_sorted, 0.95),
        "frac_frames_under_6_movers": sum(1 for m in movers if m < 6) / len(movers),
    }

    # ══════════════════════════════════════════════════════
    # 요약 — 참조값과 직접 대조할 스칼라들
    # ══════════════════════════════════════════════════════
    of = [r for r in out["per_player"] if r["pos"] != _GK]
    gk = [r for r in out["per_player"] if r["pos"] == _GK]
    sp_all = sorted(all_speeds)
    summary = {
        "dist_per_outfielder_km": sum(r["dist_km"] for r in of) / len(of),
        "dist_gk_km": (sum(r["dist_km"] for r in gk) / len(gk)) if gk else float("nan"),
        "speed_mean_ms": sum(sp_all) / len(sp_all),
        "speed_p95_ms": _pct(sp_all, 0.95),
        "speed_max_ms": sp_all[-1],
        "sprints_per_outfielder": sum(r["sprints"] for r in of) / len(of),
        "stationary_frac": sum(r["stationary_frac"] for r in of) / len(of),
        "team_length_m": _st.mean(out["teams"][s]["team_length_m"] for s in ("home", "away")),
        "team_width_m": _st.mean(out["teams"][s]["team_width_m"] for s in ("home", "away")),
        "compactness_m": _st.mean(out["teams"][s]["compactness_m"] for s in ("home", "away")),
        "def_fwd_gap_m": _st.mean(out["teams"][s]["def_fwd_gap_m"] for s in ("home", "away")),
        "press_within_5m": out["ball"]["press_within_5m"],
        "press_within_10m": out["ball"]["press_within_10m"],
        "movers_per_frame": out["movers"]["movers_per_frame"],
        "centroid_ball_corr": _st.mean(out["teams"][s]["centroid_ball_corr"] for s in ("home", "away")),
        "lateral_share": sum(r["lateral_share"] for r in of) / len(of),
    }
    if any("leash_mean" in r for r in of):
        ls = [r for r in of if "leash_mean" in r]
        summary["home_leash_mean_m"] = sum(r["leash_mean"] for r in ls) / len(ls)
        summary["home_leash_p95_m"] = sum(r["leash_p95"] for r in ls) / len(ls)
    out["summary"] = summary
    return out


def aggregate(reports):
    """여러 경기 리포트의 summary를 평균낸다(경기 간 분산이 크므로 항상
    여러 판을 돌려서 봐야 한다)."""
    keys = reports[0]["summary"].keys()
    agg = {}
    for k in keys:
        vals = [r["summary"][k] for r in reports
                if k in r["summary"] and not math.isnan(r["summary"][k])]
        if vals:
            agg[k] = {"mean": sum(vals) / len(vals),
                      "sd": _st.pstdev(vals) if len(vals) > 1 else 0.0,
                      "min": min(vals), "max": max(vals)}
    return agg


def format_report(agg_or_report, title="움직임 계측"):
    """참조값 대비표를 사람이 읽을 수 있게 찍는다."""
    agg = agg_or_report.get("summary") if "summary" in agg_or_report else agg_or_report
    if agg and not isinstance(next(iter(agg.values())), dict):
        agg = {k: {"mean": v, "sd": 0.0, "min": v, "max": v} for k, v in agg.items()}

    lines = []
    lines.append("=" * 78)
    lines.append(f"  {title}")
    lines.append("=" * 78)
    lines.append(f"{'지표':<26}{'측정값':>12}{'  실축 참조':>18}   판정")
    lines.append("-" * 78)
    verdicts = []
    for key, (lo, hi, label) in REFERENCE.items():
        if key not in agg:
            continue
        m = agg[key]["mean"]
        sd = agg[key]["sd"]
        if m < lo:
            ratio = m / lo if lo else 0.0
            mark = f"낮음 ({ratio*100:.0f}% of 하한)"
            verdicts.append((key, label, m, lo, hi, "low"))
        elif m > hi:
            mark = f"높음 ({m/hi:.2f}배)"
            verdicts.append((key, label, m, lo, hi, "high"))
        else:
            mark = "정상"
        val = f"{m:8.2f}±{sd:.2f}" if sd else f"{m:8.2f}"
        lines.append(f"{label:<26}{val:>12}{f'{lo:g} ~ {hi:g}':>18}   {mark}")
    lines.append("-" * 78)

    bad = [v for v in verdicts]
    if bad:
        lines.append("")
        lines.append("  ▸ 참조 범위를 벗어난 항목")
        for key, label, m, lo, hi, kind in bad:
            arrow = "▼" if kind == "low" else "▲"
            lines.append(f"    {arrow} {label:<24} {m:8.2f}   (기대 {lo:g}~{hi:g})")
    else:
        lines.append("  전 항목 참조 범위 내.")
    lines.append("=" * 78)
    return "\n".join(lines)


def format_detail(report):
    """포지션 그룹별 세부 — 어느 그룹이 특히 안 움직이는지 본다."""
    rows = {}
    for r in report["per_player"]:
        rows.setdefault(r["group"], []).append(r)
    lines = ["", "  ▸ 포지션 그룹별", "  " + "-" * 74,
             f"  {'그룹':<6}{'n':>3}{'이동km':>9}{'평균m/s':>9}{'최고m/s':>9}"
             f"{'스프린트':>9}{'정지%':>8}{'이탈m':>8}"]
    for g in ("GK", "DEF", "MID", "FWD"):
        rs = rows.get(g) or []
        if not rs:
            continue
        leash = [r["leash_mean"] for r in rs if "leash_mean" in r]
        lines.append(
            f"  {g:<6}{len(rs):>3}"
            f"{sum(r['dist_km'] for r in rs)/len(rs):>9.2f}"
            f"{sum(r['speed_mean'] for r in rs)/len(rs):>9.2f}"
            f"{max(r['speed_max'] for r in rs):>9.2f}"
            f"{sum(r['sprints'] for r in rs)/len(rs):>9.1f}"
            f"{100*sum(r['stationary_frac'] for r in rs)/len(rs):>8.1f}"
            f"{(sum(leash)/len(leash)) if leash else float('nan'):>8.1f}")
    lines.append("  " + "-" * 74)
    return "\n".join(lines)