"""
sensitivity.py — 후보 결과가 가정에 얼마나 의존하는가

기존 244곳 결과(`outputs/placement_grades.csv`)는 건드리지 않는다. **대표 후보
9곳만** 다른 설정으로 다시 돌려 **설정 간 순위가 유지되는지**를 본다.

    .venv/Scripts/python.exe src/sensitivity.py            # 전부 돌리고 보고
    .venv/Scripts/python.exe src/sensitivity.py --report   # 이미 있는 결과로 보고만
    .venv/Scripts/python.exe src/sensitivity.py --only wait_first,near_first

**시드는 42 한 번이다.** 절대값이 아니라 설정 간 순서 유지를 보는 것이라 반복이
필요 없다 — 같은 시드·같은 콜 ID 스트림을 쓰므로 설정 간 차이에 난수가 섞이지
않는다(`uniform_by_id`). 시드 반복은 244곳 본실행이 이미 3회로 했고, 거기서 나온
`rate_sd` 를 **설정 간 차이가 잡음보다 큰지 재는 자**로 그대로 가져다 쓴다.

─────────────────────────────────────────────────────────────
대상 후보 — 결과를 보기 전에 규칙으로 고정한다
─────────────────────────────────────────────────────────────

`select_candidates()` — 표본 부족(3km 콜 < 2,000건)을 뺀 232곳을 개선율 순으로
세우고 **상위 3 · 중앙 3 · 하위 3** 을 뽑는다. 결과를 보고 고르면 사후 선택이다.

**표본 부족을 빼는 이유** — 그 12곳은 대비 구간 폭 중앙이 2.87%p 로 정상 232곳의
1.68%p 보다 넓다. 설정을 바꿔 개선율이 1%p 움직여도 원래 구간이 3%p 짜리면
그것이 설정 탓인지 잡음인지 가릴 수 없다. **민감도 신호가 묻힌다.**

─────────────────────────────────────────────────────────────
설정 — 무엇을 흔드는가
─────────────────────────────────────────────────────────────

**1. 배차 점수 가중치(A-18).** 원문 값은 접수순서 15 + 경과대기 35 + 거리 40 인데,
접수순서와 경과대기는 **한 시점에서 정렬이 같다** — 먼저 접수된 콜이 곧 오래
기다린 콜이다. 실질은 `대기 50 : 근접 40` 한 개의 손잡이다. 그 축을 따라 흔든다.
대기쪽 50 안의 15:35 비율은 그대로 유지해 나눈다(원문 구조를 지키려는 것이다).

**2. 평일 심야 탐색 반경(A-17).** 원문에 없는 구간이라 12km 로 본 것이 A-17 이다.
주간값 7km 로 갈아끼워 대조한다. 심야(0~5시) 콜이 1.9%뿐이라 영향이 작을 것으로
보이나 확인이 필요하다.

**3. 휴게 임계 θ(A-01).** 이미 훑어 기각했고 기구만 남겨 뒀다
(`IDLE_BREAK_THETA_MIN = None`). 기각 근거는 실측 대조였고, **"θ 를 켜면 후보
순위가 달라지나"는 아직 답한 적이 없다.** θ 는 공급 자체를 줄이므로 거점 효과의
크기를 바꿀 수 있다.

─────────────────────────────────────────────────────────────
읽는 법
─────────────────────────────────────────────────────────────

- 순위가 유지되면 → 그 가정에 결론이 의존하지 않는다
- 순위가 뒤집히면 → 그 가정이 결과를 좌우한다. 어느 후보에서 갈리는지 본다

**절대값은 따로 읽는다.** 순위가 유지돼도 개선율의 크기가 크게 움직이면 "몇 %
줄어든다"는 인용은 그 설정에 의존한다.

**「상위 N곳」이라고 쓰지 않는다.** 등급·순위를 표시하지 않기로 한 것과 같은
이유다(`evaluate.assign_grades`) — 몇 곳을 묶어 보여 주는 순간 그 경계가 판단을
대신 내려 준다. 게다가 그 경계 자체가 설정에 달렸다는 것이 이 민감도의 결과다:
근접 강화에서 3위와 4위의 간격이 4.12 → 0.59%p 로 좁아진다. **개선율이 큰 쪽과
작은 쪽은 어느 설정에서도 갈리지만, 상위권 안에서 몇 곳을 묶을지는 배차 가중치에
따라 달라진다.** 아래 표의 「앞쪽 3자리」는 민감도를 재려고 밴드를 사전 고정한
결과이지 상위권의 크기에 대한 주장이 아니다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import evaluate as E
import load
import metrics as M
import simulator as S
import travel_time as T

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "outputs"
RESULT_PATH = OUT_DIR / "sensitivity_eval.csv"

# 민감도는 시드 1회다(위 모듈 주석). 244곳 본실행의 첫 시드와 같은 값을 써야
# 기본 설정 행이 `placement_eval.csv` 의 seed 42 행과 **정확히 일치**한다 —
# 그 일치 자체가 배선이 맞다는 점검이 된다(`--report` 가 대조한다).
SEED = S.SEED

# 대상 후보 수 — 상위 · 중앙 · 하위 각 몇 곳. 결과를 보기 전에 고정.
BAND_N = 3

# ─────────────────────────────────────────────────────────────
# 설정 목록
# ─────────────────────────────────────────────────────────────
#
# 대기쪽(order+wait) : 근접쪽(dist) 비율만 바꾸고, 대기쪽 안의 15:35 는 유지한다.
#   기본     50 : 40   원문 그대로(15 / 35 / 40)
#   근접강화 40 : 50   12 / 28 / 50
#   대기강화 60 : 30   18 / 42 / 30


def _score_w(wait_side: float, dist: float) -> dict:
    """대기쪽 총량과 거리 가중치로 세 항을 만든다. 15:35 비율은 유지."""
    return {"order": wait_side * 0.30, "wait": wait_side * 0.70, "dist": dist}


CONFIGS = [
    # (키, 표시명, 흔드는 가정, run_placement 에 넘길 인자)
    ("base",       "기본 (50:40)",        "—",    {}),
    ("near_first", "근접 강화 (40:50)",   "A-18", {"score_w": _score_w(40, 50)}),
    ("wait_first", "대기 강화 (60:30)",   "A-18", {"score_w": _score_w(60, 30)}),
    ("radius7",    "심야 반경 7km",       "A-17", {"radius_predawn": 7.0}),
    ("theta120",   "θ = 120분",           "A-01", {"theta": 120.0}),
    ("theta60",    "θ = 60분",            "A-01", {"theta": 60.0}),
]
CONFIG_BY_KEY = {k: (label, assa, kw) for k, label, assa, kw in CONFIGS}


# ─────────────────────────────────────────────────────────────
# 대상 후보
# ─────────────────────────────────────────────────────────────

def select_candidates(graded: pd.DataFrame = None, n: int = BAND_N) -> pd.DataFrame:
    """상위 n · 중앙 n · 하위 n. **표본 부족은 제외한다.**

    중앙은 정렬 후 중앙 색인 `mid = len//2` 를 가운데 두고 좌우로 편다 — 짝수
    개면 중앙값이 두 후보 사이에 떨어지므로 색인 하나를 중심으로 잡는다.

    좌표(`lat`/`lon`)는 집계표에 없어 후보 표에서 붙인다 — 3km 범위 판정에 쓴다.
    """
    if graded is None:
        graded = pd.read_csv(E.GRADE_PATH, encoding="utf-8-sig")
    ok = graded[graded["sample_ok"]].sort_values("rate_pct").reset_index(drop=True)
    if len(ok) < 3 * n:
        raise ValueError(f"표본 부족을 뺀 후보가 {len(ok)}곳뿐이라 {3 * n}곳을 못 뽑는다")
    mid = len(ok) // 2
    idx = (list(range(n))
           + list(range(mid - n // 2, mid - n // 2 + n))
           + list(range(len(ok) - n, len(ok))))
    sel = ok.loc[idx].copy()
    sel["band"] = ["상"] * n + ["중"] * n + ["하"] * n
    xy = E.candidate_table()[["cand_id", "lat", "lon"]]
    return sel.merge(xy, on="cand_id", how="left").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────

_COLUMNS = ["config", "label", "assa", "cand_id", "cand_name", "gu", "band",
            "n_dong_scope", "n_calls_scope", "before", "after", "delta",
            "rate_pct", "elapsed_s"]


def load_results(path: Path = RESULT_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    return pd.read_csv(path, encoding="utf-8-sig")


def _append(row: dict, path: Path = RESULT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists()
    pd.DataFrame([row], columns=_COLUMNS).to_csv(
        path, mode="a", header=header, index=False, encoding="utf-8-sig")


def _kwargs(kw: dict, gaps: np.ndarray) -> dict:
    """설정 인자를 `run_placement` 인자로 편다. θ 는 IdleBreak 로 감싼다."""
    out = dict(kw)
    theta = out.pop("theta", None)
    if theta is not None:
        out["idle_break"] = S.IdleBreak(gaps, theta)
    return out


def run_grid(keys=None, path: Path = RESULT_PATH) -> pd.DataFrame:
    """설정 × 후보를 돌려 CSV 에 덧붙인다. **이미 있는 조합은 건너뛴다.**

    설정마다 before(현행 배치)를 한 번씩 다시 돌린다 — 설정이 바뀌면 기준선도
    같이 움직이므로 244곳 본실행의 before 를 재사용할 수 없다. 개선율은 **그
    설정 안에서의 before 대비**여야 배치 효과만 남는다.
    """
    keys = list(keys or [k for k, *_ in CONFIGS])
    done = load_results(path)
    have = set(zip(done.get("config", []), done.get("cand_id", [])))

    print("입력 로딩...")
    calls = load.load_calls()
    depots = load.load_depots()
    reservation = load.load_reservation_occupancy()
    gaps = load.load_idle_gaps()
    mx = S.TravelMatrix(T.TravelTime.build(),
                        set(calls["origin_dong_canon"].astype(str))
                        | set(calls["dest_dong_canon"].astype(str)))
    sub = S.slice_period(calls, start=S.REPRESENTATIVE_START,
                         days=S.REPRESENTATIVE_DAYS)
    dong_xy = E.dong_coords()
    sel = select_candidates()
    print(f"  대표 기간 {S.REPRESENTATIVE_START} +{S.REPRESENTATIVE_DAYS}일 · "
          f"콜 {len(sub):,} · 설정 {len(keys)}개 × 후보 {len(sel)}곳")
    print(sel[["cand_id", "cand_name", "gu", "band", "n_calls_scope",
               "rate_pct", "rate_sd"]].to_string(index=False))

    for key in keys:
        label, assa, kw = CONFIG_BY_KEY[key]
        todo = [r for r in sel.itertuples() if (key, r.cand_id) not in have]
        if not todo:
            print(f"\n[{key}] {label} — 이미 끝남")
            continue
        print(f"\n[{key}] {label} ({assa}) — {len(todo)}곳 남음")
        rk = _kwargs(kw, gaps)

        t0 = time.perf_counter()
        base_log = S.run_placement(None, sub, depots, mx, reservation=reservation,
                                   seed=SEED, **rk)
        print(f"  before {time.perf_counter() - t0:.0f}초")

        for r in todo:
            members = E.scope_members(r.lat, r.lon, dong_xy)
            t0 = time.perf_counter()
            log = S.run_placement(int(r.cand_id), sub, depots, mx,
                                  reservation=reservation, seed=SEED, **rk)
            elapsed = time.perf_counter() - t0
            b, n_calls = E.scope_pickup(base_log, members)
            a, _ = E.scope_pickup(log, members)
            _append({
                "config": key, "label": label, "assa": assa,
                "cand_id": int(r.cand_id), "cand_name": r.cand_name, "gu": r.gu,
                "band": r.band, "n_dong_scope": len(members),
                "n_calls_scope": n_calls, "before": b, "after": a,
                "delta": a - b, "rate_pct": (a - b) / b * 100 if b else np.nan,
                "elapsed_s": elapsed,
            }, path)
            print(f"    {r.cand_id:>4} {r.cand_name[:16]:<18} "
                  f"{(a - b) / b * 100:+.2f}%  ({elapsed:.0f}초)")
    return load_results(path)


# ─────────────────────────────────────────────────────────────
# 보고
# ─────────────────────────────────────────────────────────────

def pivot_rates(res: pd.DataFrame) -> pd.DataFrame:
    """행 = 후보(기본 설정 개선율 순), 열 = 설정. 값은 개선율(%)."""
    order = [k for k, *_ in CONFIGS if k in set(res["config"])]
    p = res.pivot_table(index=["cand_id", "cand_name", "band"],
                        columns="config", values="rate_pct")
    p = p[order].sort_values("base").reset_index()
    return p


def spearman_table(p: pd.DataFrame) -> pd.DataFrame:
    """기본 설정 대비 순위 상관 + 개선율 절대 이동폭."""
    base = p["base"].to_numpy(float)
    rows = []
    for key, label, assa, _ in CONFIGS:
        if key not in p.columns:
            continue
        v = p[key].to_numpy(float)
        rho, pval = stats.spearmanr(base, v)
        d = v - base
        # 순위가 실제로 몇 칸 움직였나 — ρ 하나로는 "어디서 갈렸나"를 못 본다.
        rk_b = pd.Series(base).rank().to_numpy()
        rk_v = pd.Series(v).rank().to_numpy()
        rows.append({
            "config": key, "label": label, "assa": assa,
            "rho": rho, "p_value": pval,
            "n_swap": int((rk_b != rk_v).sum()),
            "max_rank_move": int(np.abs(rk_b - rk_v).max()),
            "mean_rate": float(np.mean(v)),
            "d_mean": float(np.mean(d)), "d_min": float(np.min(d)),
            "d_max": float(np.max(d)), "d_absmax": float(np.abs(d).max()),
        })
    return pd.DataFrame(rows)


def report(res: pd.DataFrame, graded: pd.DataFrame = None) -> str:
    """설정별 9곳 개선율 · 순위 상관 · 절대 이동 + 잡음 대비 판정."""
    if graded is None:
        graded = pd.read_csv(E.GRADE_PATH, encoding="utf-8-sig")
    sd = graded.set_index("cand_id")["rate_sd"]
    p = pivot_rates(res)
    tab = spearman_table(p)
    L = []

    L.append("■ 설정별 개선율(%) — 행은 기본 설정 기준 순, 시드 42 1회")
    head = f"  {'후보':<22}{'대':>3}{'σ':>7}" + "".join(
        f"{CONFIG_BY_KEY[c][0][:11]:>13}" for c in p.columns[3:])
    L.append(head)
    for r in p.itertuples():
        name = f"{r.cand_id} {r.cand_name[:15]}"
        cells = "".join(f"{getattr(r, c):>13.2f}" for c in p.columns[3:])
        L.append(f"  {name:<22}{r.band:>3}{sd.get(r.cand_id, np.nan):>7.2f}{cells}")

    L.append("")
    L.append("■ 기본 설정 대비 순위 상관(Spearman, n=9)과 절대 이동")
    L.append(f"  {'설정':<20}{'가정':>5}{'ρ':>7}{'p':>8}{'순위바뀜':>9}"
             f"{'최대이동':>9}{'평균개선율':>11}{'Δ평균':>8}{'Δ최대':>8}")
    for r in tab.itertuples():
        L.append(f"  {r.label:<20}{r.assa:>5}{r.rho:>7.3f}{r.p_value:>8.4f}"
                 f"{r.n_swap:>9}{r.max_rank_move:>9}{r.mean_rate:>11.2f}"
                 f"{r.d_mean:>+8.2f}{r.d_absmax:>8.2f}")

    L.append("")
    L.append("■ 설정 간 차이가 시드 잡음보다 큰가 — 후보별 |Δ| vs 그 후보의 σ")
    for key in p.columns[3:]:
        if key == "base":
            continue
        d = (p[key] - p["base"]).abs().to_numpy()
        s = sd.reindex(p["cand_id"]).to_numpy(float)
        over = np.flatnonzero(d > s)
        label = CONFIG_BY_KEY[key][0]
        if len(over) == 0:
            L.append(f"  {label:<20} |Δ| 가 σ 를 넘는 후보 **없음** "
                     f"(최대 |Δ| {d.max():.2f} vs σ {s[int(np.argmax(d))]:.2f})")
        else:
            who = ", ".join(f"{int(p['cand_id'][i])} {p['cand_name'][i][:10]}"
                            f"(|Δ|{d[i]:.2f} vs σ{s[i]:.2f})" for i in over)
            L.append(f"  {label:<20} {len(over)}곳에서 σ 초과 — {who}")

    L.append("")
    L.append("■ 밴드는 보존되는가 — 상 밴드 3곳이 설정마다 여전히 앞쪽 3자리인가")
    L.append(f"  {'설정':<20}{'앞쪽 3자리':>12}{'상/중 간격':>12}{'중·하 6곳 ρ':>13}")
    top_ids = set(p.loc[p["band"] == "상", "cand_id"])
    lower = p["band"] != "상"
    for key in p.columns[3:]:
        s = p.sort_values(key)
        kept = set(s["cand_id"].head(len(top_ids))) == top_ids
        gap = abs(s[key].iloc[len(top_ids)] - s[key].iloc[len(top_ids) - 1])
        rho_low, _ = stats.spearmanr(p.loc[lower, "base"], p.loc[lower, key])
        L.append(f"  {CONFIG_BY_KEY[key][0]:<20}{'예' if kept else '**아니오**':>12}"
                 f"{gap:>12.2f}{rho_low:>13.3f}")
    L.append("  상/중 간격 = 3위와 4위의 개선율 차(%p).")
    L.append("  **개선율이 큰 쪽과 작은 쪽은 어느 설정에서도 갈린다. 다만 상위권 안에서")
    L.append("  몇 곳을 묶을지는 배차 가중치에 따라 달라진다** — 위 간격이 σ 수준으로")
    L.append("  좁아지는 설정이 있다. 「앞쪽 3자리」는 민감도를 재려고 밴드를 사전")
    L.append("  고정한 결과이지 상위권의 크기에 대한 주장이 아니다.")

    L.append("")
    L.append("  ※ σ 는 244곳 본실행(시드 3회)의 `rate_sd` 다 — 민감도는 시드 1회라")
    L.append("     자체 σ 가 없다. 설정 간 차이를 그 후보의 시드 잡음과 견주는 자로만 쓴다.")
    L.append("  ※ **n=9 의 Spearman 은 거칠다.** ρ=1.0 이어도 '9곳 안에서 순서가")
    L.append("     안 바뀌었다'는 뜻이지 244곳 전체 순위가 보존된다는 뜻이 아니다.")
    return "\n".join(L)


def check_base(res: pd.DataFrame) -> str:
    """기본 설정 행이 244곳 본실행의 seed 42 행과 같은지 — 배선 점검.

    같은 코드·같은 시드·같은 콜 ID 스트림이라 **정확히 일치해야 한다.** 어긋나면
    민감도 배선이 본실행과 다른 것이므로 나머지 비교도 못 믿는다.
    """
    base = res[res["config"] == "base"]
    if base.empty:
        return "  (기본 설정 결과가 없어 대조 못 함)"
    ev = E.load_results()
    ev = ev[(ev["seed"] == SEED) & ev["cand_id"].isin(base["cand_id"])]
    ev = ev.drop_duplicates("cand_id").set_index("cand_id")["rate_pct"]
    d = (base.set_index("cand_id")["rate_pct"] - ev.reindex(base["cand_id"])).abs()
    return (f"  기본 설정 9곳 vs placement_eval.csv seed {SEED}: "
            f"최대 차이 {d.max():.2e}%p — "
            f"{'일치' if d.max() < 1e-9 else '**어긋남**'}")


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="가정 민감도(A-01 · A-17 · A-18)")
    ap.add_argument("--report", action="store_true",
                    help="재생하지 않고 이미 있는 결과로 보고만")
    ap.add_argument("--only", type=str, default=None,
                    help="쉼표로 구분한 설정 키만 실행")
    args = ap.parse_args(argv)

    res = load_results() if args.report else run_grid(
        args.only.replace(" ", "").split(",") if args.only else None)
    if res.empty:
        print("결과가 없다")
        return
    print()
    print(report(res))
    print()
    print("■ 배선 점검")
    print(check_base(res))
    print(f"\n저장: {RESULT_PATH}")


if __name__ == "__main__":
    main()
