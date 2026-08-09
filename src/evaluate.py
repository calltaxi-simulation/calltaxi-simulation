"""
evaluate.py — 배치안 후보 평가

`outputs/dong_candidates.csv` 의 배정된 후보 244곳을 하나씩 시뮬에 넣어
**거점 3km 안 동들의 픽업이 얼마나 줄어드는가**를 잰다.

    .venv/Scripts/python.exe src/evaluate.py                      # 1단계 → 2단계 → 집계
    .venv/Scripts/python.exe src/evaluate.py --stage 1            # 1단계만
    .venv/Scripts/python.exe src/evaluate.py --stage 2 --seeds 42,43
    .venv/Scripts/python.exe src/evaluate.py --stage 2 --all --seeds 43,44
    .venv/Scripts/python.exe src/evaluate.py --grade              # 결과로 집계표만

**중간에 끊겨도 이어서 돌린다.** 후보 하나가 끝날 때마다 CSV 에 덧붙이고, 다시
실행하면 이미 있는 (cand_id, seed) 는 건너뛴다. 한 번에 끝나지 않는 것을 전제로 짰다.

**시드를 나눠 돌릴 수 있다**(`--seeds`). 컴퓨터를 밤새 켜둘 수 없을 때
오늘 42·43, 내일 44 식으로 쪼갠다. **바깥 루프가 시드다** — 시드 하나로 후보
전부를 돈 뒤 다음 시드로 넘어간다. 후보별로 3회씩 돌면 중간에 멈췄을 때 앞쪽
후보만 3회, 뒤쪽은 0회가 되어 **비교할 수 없는 결과가 남는다.**

─────────────────────────────────────────────────────────────
왜 이렇게 재는가 — 근거는 docs/model_flow.md 「후보 평가」 절
─────────────────────────────────────────────────────────────

**범위는 거점에서 3km.** 서울 평균으로는 후보를 가릴 수 없다 — 두 후보의 Δ픽업
차이가 0.013분인데 시드 간 표준편차가 0.039분이다. 8대는 574대의 1.4%인데 그
효과를 426개 동으로 나눠 보기 때문이다. **3km 로 좁히면 효과가 7.6배(−0.441 vs
−0.058)로 커진다 — 근거는 그것 하나다.**

"콜이 모여 잡음이 억제된다"는 부수 효과도 **244곳 실측으로 확인된다** — log-log
상관 −0.169(절대 Δ 는 −0.274, 기울기 −0.423 으로 1/√n 의 −0.5 에 가깝다),
콜 구간별 σ 중앙 1.014/0.795/0.777/0.540. 상위 50곳만 봤을 때는 +0.110 으로
관계가 없어 보였는데 그건 부분집합이 좁아서였다. docs/model_flow.md 「증차 규모」
절의 「정정의 정정」 참조. **다만 3km 채택 근거는 여전히 효과 크기 7.6배다.**

**3km 는 집계 범위이지 계산 단위가 아니다.** 시뮬은 여전히 동 단위로 돌고,
결과를 읽을 때 거점 3km 안에 드는 동들(평균 25.4개)의 픽업을 모아 볼 뿐이다.

**배정동 하나로 더 좁히면 못 쓴다.** 효과는 가장 크지만(−0.485) 30일 동안 콜이
53~245건뿐이라 σ 가 0.743 으로 튄다(신호/잡음 0.74).

**주 지표는 개선율(%)이다.** 3km 범위의 before 픽업이 후보마다 12.1~14.7분으로
달라, 절대 Δ 로 비교하면 원래 픽업이 긴 지역이 자동으로 유리해진다 — 그건 거점
효과가 아니라 출발점 차이다. 절대 Δ 는 병기한다.

**결과에 등급을 표시하지 않는다.** 등급 계산(`assign_grades`)은 남아 있지만
표와 CSV 에서는 `ref_` 접두사가 붙은 참고 열로 내렸다 — 244곳을 돌려 보니 등급이
[상] 4곳 / [하] 240곳으로 나와 표시 자체가 판단을 돕지 못하고, 무엇보다 [상] 4곳
중 3곳이 표본 부족이라 "최고 등급"이 **가장 좁은 지역을 가리켰다.** 사유는
docs/model_flow.md 「등급은 계산하되 표시하지 않는다」 절.

**대신 후보별로 이것만 낸다** — 개선율 ± 대비 구간 │ 콜 · 동 · 총절감 │ 표본 부족
표시 │ 겹치는 후보 수. 구간이 겹치면 우열을 못 가리고, 안 겹치면 가릴 수 있다.
읽는 사람이 등급 하나를 받는 대신 네 가지를 직접 본다.

**3km 콜이 적은 후보는 「표본 부족」으로 표시한다**(`MIN_CALLS_SCOPE`). 범위가
좁으면 개선율이 커 보이지만 혜택이 닿는 사람이 적다. 계산에서 빼지는 않는다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import load
import metrics as M
import simulator as S
import travel_time as T

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "outputs"
RESULT_PATH = OUT_DIR / "placement_eval.csv"
GRADE_PATH = OUT_DIR / "placement_grades.csv"

# 평가 범위(km). metrics.SUPPLY_RADIUS_KM 과 같은 값이다 — 진단의 공급 근접도와
# 같은 반경을 써야 "3km 안에 차가 없는 동"과 "거점을 놓아 3km 안에 넣는다"가
# 같은 자로 읽힌다.
RADIUS_KM = M.SUPPLY_RADIUS_KM

# 2단계 대상 수. **결과를 보기 전에 고정한다** — 보고 정하면 사후 선택이다.
#
# **`--all` 을 쓰면 이 컷은 쓰이지 않는다(244곳 전부).** 컷은 성능 때문에 둔
# 타협이었지 그쪽이 더 옳아서가 아니다 — 대표 기간을 한 달로 잡은 근거가 "성능
# 제약이 없으니 전부 3회 돌릴 수 있다"였으므로, 상위 50곳만 3회로 두면 그 전제가
# 무너진다. 전부 돌리면 고를 일이 없어 사후 선택 문제 자체가 사라진다.
STAGE2_TOP_N = 50

STAGE1_SEEDS = (S.SEED,)
STAGE2_SEEDS = (42, 43, 44)

# 등급 경계 — 인접 후보 간 차이가 이 배수의 σ 이상이면 등급을 가른다.
GRADE_SIGMA_K = 2.0

# 대비 구간의 반폭 배수. **등급 규칙에서 유도한 값이지 새 규칙이 아니다.**
# 두 구간이 안 겹칠 조건은 `gap ≥ c(σᵢ+σⱼ)`, 등급이 갈릴 조건은
# `gap ≥ k·√(σᵢ²+σⱼ²)` 다. σᵢ=σⱼ 일 때 둘이 같아지는 c 는 k/√2 = 1.414 이다.
# 이 값을 쓰면 **"구간이 겹친다" 와 "같은 등급이다" 가 같은 말이 된다** — 표에서
# 눈으로 읽은 것과 등급이 어긋나지 않는다. k 를 바꾸면 여기도 따라 바뀐다.
COMPARISON_K = GRADE_SIGMA_K / 2 ** 0.5

# 3km 범위 콜이 이보다 적으면 **표본 부족**으로 표시한다(계산에서 빼지는 않는다).
# 유도 — 3km 를 택한 근거가 콜 4,427건에서 신호/잡음 2.55 였다. 잡음이 √n 로
# 줄면 S/N ∝ √n 이므로 S/N 을 1.7(2.55 의 2/3) 위로 두려면
# 4,427 × (1.7/2.55)² ≈ 1,967 건이 필요하다.
#
# **유도의 전제(콜이 모이면 잡음이 준다)는 244곳 × 3시드로 확인됐다**(08.10).
# log-log 상관 −0.169, 절대 Δ 기울기 −0.423. 상위 50곳만 볼 때는 +0.09~+0.11 로
# 관계가 없어 보였는데 그 부분집합이 콜 수 범위가 좁았기 때문이다. 표본 부족
# 12곳의 대비 구간 폭 중앙은 2.87%p 로 정상 232곳의 1.68%p 보다 넓다.
#
# **표시의 의미는 잡음과 수혜 규모 둘 다다** — 동 2개·콜 666건에 걸린 개선율과
# 동 30개·콜 6,000건에 걸린 개선율은 같은 무게로 읽을 수 없다.
#
# **기준값 2,000 은 그대로 둔다.** 관계가 확인됐다고 문턱을 옮기면 결과를 보고
# 규칙을 고치는 것이라 사후 선택이다.
MIN_CALLS_SCOPE = 2000


# ─────────────────────────────────────────────────────────────
# 입력
# ─────────────────────────────────────────────────────────────

def candidate_table() -> pd.DataFrame:
    """평가 대상 후보. 동에 배정된 곳만(244곳).

    배정되지 않은 옥외 후보 104곳은 어느 동의 거점도 되지 않아 시뮬에 넣을
    이유가 없다. 한 후보가 여러 동을 맡기도 해서 `dongs` 에 모아 둔다.
    """
    path = OUT_DIR / "dong_candidates.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없다 — src/candidates.py 를 먼저 돌릴 것")
    c = pd.read_csv(path, encoding="utf-8-sig")
    c = c[c["is_assigned"] & c["cand_id"].notna()].copy()
    g = (c.assign(label=c["gu"] + " " + c["dong_canon"])
          .groupby("cand_id")
          .agg(cand_name=("cand_name", "first"), lat=("cand_lat", "first"),
               lon=("cand_lon", "first"), capacity=("capacity", "first"),
               gu=("gu", "first"),
               dongs=("label", lambda s: " · ".join(sorted(s))),
               n_dong_assigned=("label", "size"))
          .reset_index())
    g["cand_id"] = g["cand_id"].astype(int)
    return g.sort_values("cand_id").reset_index(drop=True)


def dong_coords() -> pd.DataFrame:
    """동 중심점. 3km 판정에 쓴다."""
    dm = pd.read_csv(OUT_DIR / "dong_metrics.csv")
    return dm[["gu", "dong_canon", "lat", "lon"]].dropna().reset_index(drop=True)


def scope_members(lat: float, lon: float, dong_xy: pd.DataFrame,
                  radius_km: float = RADIUS_KM) -> set:
    """거점에서 반경 안에 드는 동 집합. 직선거리(haversine)다."""
    km = T.haversine_km(lat, lon, dong_xy["lat"].to_numpy(float),
                        dong_xy["lon"].to_numpy(float))
    return {k for k, v in zip(zip(dong_xy["gu"], dong_xy["dong_canon"]), km)
            if v <= radius_km}


def scope_pickup(log: pd.DataFrame, members: set) -> tuple[float, int]:
    """범위 안 콜의 픽업 평균과 건수. **콜 가중**이다.

    동 균등가중이 아닌 이유 — 여기서 재는 것은 형평이 아니라 '그 지역 이용자가
    평균 얼마나 덜 기다리게 되나'다. 동 균등으로 재면 콜 50건짜리 동이 5,000건
    짜리 동과 같은 무게가 되어, 실제로 혜택을 받는 사람 수와 어긋난다.
    형평은 픽업 지니로 따로 본다(metrics.pickup_gini).
    """
    d = log.assign(_p=M.pickup_min(log))
    d = d[d["_p"].notna()]
    key = pd.MultiIndex.from_arrays([d["origin_gu"].astype(str),
                                     d["origin_dong_canon"].astype(str)])
    hit = key.isin(members)
    return float(d.loc[hit, "_p"].mean()), int(hit.sum())


# ─────────────────────────────────────────────────────────────
# 실행 — 중간 저장 · 이어 돌리기
# ─────────────────────────────────────────────────────────────

_COLUMNS = ["stage", "cand_id", "cand_name", "gu", "dongs", "capacity", "seed",
            "n_dong_scope", "n_calls_scope", "before", "after", "delta",
            "rate_pct", "elapsed_s"]


def load_results(path: Path = RESULT_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    return pd.read_csv(path, encoding="utf-8-sig")


def _append(row: dict, path: Path = RESULT_PATH):
    """한 줄씩 덧붙이고 즉시 flush. 중간에 끊겨도 여기까지는 남는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists()
    pd.DataFrame([row], columns=_COLUMNS).to_csv(
        path, mode="a", header=header, index=False, encoding="utf-8-sig")


def run_stage(stage: int, cands: pd.DataFrame, seeds, *, calls, depots, mx,
              reservation, dong_xy, path: Path = RESULT_PATH) -> pd.DataFrame:
    """후보 × 시드를 돌려 CSV 에 덧붙인다. **이미 있는 조합은 건너뛴다.**

    **바깥 루프가 시드다.** 시드 하나로 후보 전부를 돈 뒤 다음 시드로 넘어간다 —
    후보별로 시드를 다 돌면 중간에 멈췄을 때 앞쪽 후보만 반복이 쌓이고 뒤쪽은
    비어 σ 를 낼 수 없는 결과가 남는다.

    before 는 시드마다 한 번만 돌리고 메모리에 들고 있는다 — 후보마다 범위가
    달라 집계는 다시 하지만, 재생 자체는 배치안이 같으면 같은 결과다.
    """
    done = load_results(path)
    have = set(zip(done.get("cand_id", []), done.get("seed", [])))

    baseline: dict[int, pd.DataFrame] = {}
    todo = [(r, s) for s in seeds for r in cands.itertuples()
            if (r.cand_id, s) not in have]
    print(f"[{stage}단계] 후보 {len(cands)} × 시드 {len(seeds)} = "
          f"{len(cands) * len(seeds)}건 중 **{len(todo)}건** 남음")

    for i, (cand, seed) in enumerate(todo, 1):
        if seed not in baseline:
            t0 = time.perf_counter()
            baseline[seed] = S.run_placement(None, calls, depots, mx,
                                             reservation=reservation, seed=seed)
            print(f"  before(seed {seed}) {time.perf_counter() - t0:.0f}초")

        members = scope_members(cand.lat, cand.lon, dong_xy)
        t0 = time.perf_counter()
        log = S.run_placement(int(cand.cand_id), calls, depots, mx,
                              reservation=reservation, seed=seed)
        elapsed = time.perf_counter() - t0

        b, n_calls = scope_pickup(baseline[seed], members)
        a, _ = scope_pickup(log, members)
        _append({
            "stage": stage, "cand_id": int(cand.cand_id),
            "cand_name": cand.cand_name, "gu": cand.gu, "dongs": cand.dongs,
            "capacity": int(cand.capacity), "seed": int(seed),
            "n_dong_scope": len(members), "n_calls_scope": n_calls,
            "before": b, "after": a, "delta": a - b,
            "rate_pct": (a - b) / b * 100 if b else np.nan,
            "elapsed_s": elapsed,
        }, path)
        if i % 10 == 0 or i == len(todo):
            left = (len(todo) - i) * elapsed / 60
            print(f"  {i}/{len(todo)}  cand {cand.cand_id} "
                  f"{cand.cand_name[:14]} {(a - b) / b * 100:+.2f}%  "
                  f"(남은 예상 {left:.0f}분)")
    return load_results(path)


def parse_seeds(text: str | None) -> tuple:
    """`--seeds 42,43` → (42, 43). 없으면 빈 튜플(단계 기본값을 쓴다)."""
    if not text:
        return ()
    return tuple(int(x) for x in text.replace(" ", "").split(",") if x)


def stage2_shortlist(results: pd.DataFrame, top_n: int = STAGE2_TOP_N) -> list:
    """1단계 결과에서 2단계 대상 상위 N. **개선율 기준**이다.

    1단계는 시드 1회라 σ 가 없다 — **등급을 매기지 않고 정렬만 한다.**
    컷 N 은 결과를 보기 전에 고정된 값이다(사후 선택 차단).

    **`--all` 로 244곳 전부를 돌면 이 함수는 쓰이지 않는다.** 컷이 없으면 고를
    일도 없어 사후 선택 문제 자체가 사라진다 — 컷은 성능 때문에 둔 타협이지
    그쪽이 더 옳아서가 아니었다.
    """
    s1 = results[results["stage"] == 1]
    if s1.empty:
        raise ValueError("1단계 결과가 없다")
    return (s1.groupby("cand_id")["rate_pct"].mean()
              .nsmallest(top_n).index.tolist())


def seeds_missing(results: pd.DataFrame, target_ids, seeds) -> list:
    """대상 후보 중 **하나라도** 빠뜨린 시드. 등급 보류 판정에 쓴다.

    후보별로 따진다 — "어느 한 후보라도 그 시드가 있으면 됐다"로 보면 194곳을
    이어 돌리는 중간에 등급이 매겨진다.
    """
    df = results[results["cand_id"].isin(list(target_ids))]
    have = df.drop_duplicates(["cand_id", "seed"]).groupby("cand_id")["seed"].agg(set)
    if len(have) < len(set(target_ids)):
        return list(seeds)
    return [s for s in seeds if not all(s in v for v in have)]


# ─────────────────────────────────────────────────────────────
# 후보 집계표 (등급은 참고 열로만 남는다)
# ─────────────────────────────────────────────────────────────

# 후보 한 곳이 읽히는 순서. **개선율 → 그 불확실성 → 규모 → 경고** 순으로 두어
# 왼쪽부터 읽으면 "얼마나 좋은가 / 얼마나 확실한가 / 몇 명에게 닿는가"가 차례로
# 나오게 했다. 등급 3열은 맨 끝 참고 열이다.
_GRADE_COLUMNS = [
    "cand_id", "cand_name", "gu", "dongs", "n_seed", "n_dong_scope",
    "n_calls_scope", "before", "delta", "rate_pct", "rate_sd",
    "rate_lo", "rate_hi", "interval", "sample_ok", "total_delta_min",
    "n_overlap", "rank_rate", "rank_total",
    "grade", "gap_prev", "threshold_prev",
]

# CSV 로 나갈 때 참고 열에 붙는 접두사. **열을 지우지 않는 이유** — 등급 규칙은
# 사전 고정된 규칙이라 결과를 보고 지우면 그 자체가 사후 선택이다. 재현·감사에
# 필요하므로 남기되, 이름과 위치로 "이건 판단 근거가 아니다"를 못 박는다.
_REF_COLUMNS = ("grade", "gap_prev", "threshold_prev")


def report_frame(graded: pd.DataFrame) -> pd.DataFrame:
    """CSV 로 내보낼 형태 — 등급 3열을 `ref_` 접두사로 바꿔 맨 뒤로 보낸다.

    CSV 를 여는 사람이 `grade` 열을 보면 그걸로 고른다. 열 이름 자체가
    "참고용"이라고 말하게 두는 편이 주석보다 확실하다.
    """
    return graded.rename(columns={c: f"ref_{c}" for c in _REF_COLUMNS})


def assign_grades(results: pd.DataFrame, *, stage: int = 2,
                  k: float = GRADE_SIGMA_K) -> pd.DataFrame:
    """후보별 집계표. 개선율 순 정렬 + 대비 구간 + 수혜 규모 + 겹침 수.

    **등급도 함께 계산하지만 표시하지 않는다.** 규칙은 아래 그대로 남아 있고
    `grade`·`gap_prev`·`threshold_prev` 열도 그대로 나온다 — 다만 보고와 CSV 에서는
    `ref_` 참고 열로 내려간다(`report_frame`). 규칙을 지운 것이 아니라 **표시를
    고친 것이다.** 사유는 docs/model_flow.md 「등급은 계산하되 표시하지 않는다」.

    **규칙(결과를 보기 전에 고정).** 인접 후보의 개선율 차이가 두 후보의
    σ 로 만든 문턱 `k · sqrt(σᵢ² + σⱼ²)` 이상이면 거기서 등급을 나누고,
    그보다 좁으면 같은 등급으로 묶는다.

    **고정 컷(−4% / −2% 같은)을 쓰지 않는 이유** — 그 숫자에 근거가 없어
    "왜 4%냐"에 답할 수 없고, 후보들이 실제로 비슷해도 억지로 나눈다.
    σ 기반은 **등급 수가 결과에 따라 정해진다** — 비슷하면 하나로 뭉치고
    확실히 갈리면 여러 등급이 나온다. 그게 정직하다.

    **σ 는 후보별 시드 간 표준편차다**(2단계 3회). 두 후보를 비교할 때는
    차이의 산포이므로 `sqrt(σᵢ² + σⱼ²)` 로 합친다. 평균의 표준오차(σ/√n)를
    쓰면 문턱이 √3 배 낮아져 더 잘게 갈리는데, **보수적인 쪽을 택했다** —
    등급을 잘게 나눠 놓고 "사실은 구분이 안 된다"고 적는 것보다 처음부터
    묶는 편이 오독을 덜 부른다.

    **1단계 결과에는 σ 가 없어 등급을 매기지 않는다**(정렬만). 다만 2단계 대상이
    된 후보는 1단계에서 돈 시드도 반복으로 함께 센다 — 같은 배치·같은 시드라
    결과가 같고, 버리면 σ 의 표본이 3에서 2로 준다.

    **등급이 하나로 뭉칠 수 있다 — 그건 결함이 아니라 결과다.** 이 규칙은 인접
    간격만 본다. 후보들이 촘촘히 깔리면 양 끝이 크게 벌어져 있어도 중간에 σ 만 한
    빈틈이 없어 한 등급이 된다. 그때 "구분이 안 된다"가 정직한 보고다. 244곳에서
    실제로 그렇게 됐고(240곳이 한 덩어리), **그래서 표시를 대비 구간과 겹침 수로
    바꿨다** — 구간이 안 겹치는 쌍은 등급 없이도 우열을 말할 수 있다.

    **`n_overlap` — 그 후보와 대비 구간이 겹치는 다른 후보의 수.** 등급 한 글자가
    하던 일("얘는 어느 무리인가")을 후보마다 숫자로 낸다. 0 이면 단독으로 갈리고,
    크면 그 후보의 순위는 그만큼 말할 수 없다는 뜻이다. 등급과 달리 **인접 간격이
    아니라 그 후보 자신의 구간**을 보므로 촘촘히 깔려도 뭉개지지 않는다.

    한계: σ 를 3개 표본에서 추정하므로 σ 자체가 흔들린다. 등급 경계가 한두
    후보 차이로 달라질 수 있으니 경계 근처는 같은 등급으로 읽는 편이 안전하다.

    **개선율과 수혜 규모를 함께 낸다.** 개선율만으로 정렬하면 범위가 좁은 후보가
    위를 차지한다 — 3km 안 콜이 666건인 곳의 −9.6% 와 6,000건인 곳의 −3% 는 같은
    무게가 아니다. `total_delta_min`(= delta × 콜수, 범위 안에서 사라진 총 대기
    분)과 `n_calls_scope` 를 나란히 두어 한눈에 보이게 했다. 정렬은 개선율 그대로다.

    반환: cand_id, cand_name, gu, dongs, n_seed, n_dong_scope, n_calls_scope,
          before, delta, rate_pct, rate_sd, rate_lo, rate_hi, interval,
          sample_ok, total_delta_min, n_overlap, rank_rate, rank_total,
          grade, gap_prev, threshold_prev  ← 뒤 셋은 참고 열
    """
    cids = results.loc[results["stage"] == stage, "cand_id"].unique()
    if len(cids) == 0:
        raise ValueError(f"{stage}단계 결과가 없다")
    # **단계를 가리지 않고 그 후보의 모든 시드를 쓴다.** 1단계에서 이미 돈 시드는
    # 2단계에서 건너뛰므로(같은 배치·같은 시드라 결과가 같다) 단계로 거르면 그
    # 반복을 잃는다 — 시드 3회로 잡은 σ 가 2회짜리가 되어 등급이 흔들린다.
    df = results[results["cand_id"].isin(cids)].drop_duplicates(
        subset=["cand_id", "seed"], keep="first")

    g = (df.groupby(["cand_id", "cand_name", "gu", "dongs"], as_index=False)
           .agg(n_seed=("seed", "nunique"),
                n_dong_scope=("n_dong_scope", "first"),
                n_calls_scope=("n_calls_scope", "first"),
                before=("before", "mean"), delta=("delta", "mean"),
                rate_pct=("rate_pct", "mean"), rate_sd=("rate_pct", "std")))
    if (g["n_seed"] < 2).any():
        raise ValueError("등급을 매기려면 후보마다 시드 2회 이상이 필요하다 "
                         "— σ 를 낼 수 없다")

    g = g.sort_values("rate_pct").reset_index(drop=True)   # 개선이 큰 순(음수)
    sd = g["rate_sd"].to_numpy()
    rate = g["rate_pct"].to_numpy()

    grade, gap, thr = np.empty(len(g), dtype=object), np.full(len(g), np.nan), \
        np.full(len(g), np.nan)
    cur = 0
    grade[0] = 0
    for i in range(1, len(g)):
        gap[i] = abs(rate[i] - rate[i - 1])
        thr[i] = k * float(np.sqrt(sd[i] ** 2 + sd[i - 1] ** 2))
        if gap[i] >= thr[i]:
            cur += 1
        grade[i] = cur

    labels = _grade_labels(cur + 1)
    g["grade"] = [labels[x] for x in grade]
    g["gap_prev"], g["threshold_prev"] = gap, thr

    half = COMPARISON_K * g["rate_sd"]
    g["rate_lo"], g["rate_hi"] = g["rate_pct"] - half, g["rate_pct"] + half
    g["interval"] = [f"{r:+.2f}% ± {h:.2f}%p" for r, h in zip(g["rate_pct"], half)]
    g["sample_ok"] = g["n_calls_scope"] >= MIN_CALLS_SCOPE

    # **수혜 규모.** 개선율은 출발점 픽업으로만 정규화해 "몇 사람에게 닿는가"를
    # 담지 않는다. 범위가 좁은 후보가 개선율만으로 위에 오는 것을 막으려면 규모를
    # 같이 봐야 한다. total_delta_min 은 임의 가중의 복합 지표가 아니라 **범위
    # 안에서 실제로 사라진 총 대기 분**이다(분/콜 × 콜수). 단위가 있다.
    #
    # **그렇다고 이걸로 정렬하지 않는다.** 편향이 반대로 걸릴 뿐이다 — 도심
    # 후보는 콜이 많아 개선율이 낮아도 총 절감이 크게 나온다. 어느 한쪽을 주
    # 정렬로 삼으면 반드시 그 방향으로 오독된다. **둘을 나란히 두고 읽는다.**
    # 정렬 키가 개선율인 것은 2단계 컷과 등급 규칙이 그 기준으로 사전 고정돼
    # 있기 때문이다 — 지금 바꾸면 사후 선택이 된다.
    g["total_delta_min"] = g["delta"] * g["n_calls_scope"]

    # **겹치는 후보 수.** 등급을 표시하지 않으므로 "이 후보는 몇 곳과 구분되지
    # 않는가"를 후보마다 직접 낸다. 244×244 불리언이라 메모리는 문제되지 않는다.
    lo, hi = g["rate_lo"].to_numpy(), g["rate_hi"].to_numpy()
    g["n_overlap"] = ((lo[:, None] <= hi[None, :])
                      & (hi[:, None] >= lo[None, :])).sum(axis=1) - 1

    g["rank_rate"] = g["rate_pct"].rank(method="min").astype(int)
    g["rank_total"] = g["total_delta_min"].rank(method="min").astype(int)
    return g[_GRADE_COLUMNS]


def overlaps(graded: pd.DataFrame, cand_id: int) -> pd.DataFrame:
    """한 후보와 **대비 구간이 겹치는** 후보들 — "얘랑 얘는 못 가린다"를 뽑는다.

    등급을 표시하지 않으므로 이게 "무리 짓기"의 실질적인 도구다. 겹치지 않는
    후보와는 우열을 말할 수 있다. 개수만 필요하면 `n_overlap` 열을 쓴다.
    """
    row = graded.loc[graded["cand_id"] == cand_id]
    if row.empty:
        raise ValueError(f"cand {cand_id} 가 집계표에 없다")
    lo, hi = float(row["rate_lo"].iloc[0]), float(row["rate_hi"].iloc[0])
    hit = (graded["rate_lo"] <= hi) & (graded["rate_hi"] >= lo)
    return graded.loc[hit, ["cand_id", "cand_name", "gu", "interval",
                            "n_calls_scope", "total_delta_min", "sample_ok"]]


def _grade_labels(n: int) -> list:
    """등급 이름. 2~3개면 상/중/하, 그 밖은 A/B/C… 26개를 넘으면 AA/AB…"""
    if n == 1:
        return ["단일"]
    if n == 2:
        return ["상", "하"]
    if n == 3:
        return ["상", "중", "하"]

    def name(i: int) -> str:
        # 244곳을 돌리면 등급이 26개를 넘을 수 있다. chr(ord('A')+i) 만 쓰면
        # 'Z' 를 지나 '[' 같은 문자가 나온다.
        s = ""
        while True:
            s = chr(ord("A") + i % 26) + s
            i = i // 26 - 1
            if i < 0:
                return s

    return [name(i) for i in range(n)]


def _cand_line(r) -> str:
    """후보 한 줄 — 등급 대신 내는 네 가지를 **한 줄에 나란히** 둔다.

        개선율 ± 대비 구간 │ 콜 · 동 · 총절감 │ 겹침 수 │ 표본 부족

    개선율만 보이면 범위가 좁은 후보가 위를 차지한 것도, 그 순위가 다른 수십
    곳과 구분되지 않는다는 것도 알아채지 못한다. 넷을 같은 줄에 두어 눈이 함께
    가게 한다 — 따로 찾아야 하는 정보는 보지 않는다.
    """
    flag = "" if r.sample_ok else " ※표본부족"
    return (f"      {r.cand_id:>4} {r.cand_name[:18]:<20} {r.interval:>17} │ "
            f"콜 {r.n_calls_scope:>6,} · 동 {r.n_dong_scope:>2} · "
            f"총 {r.total_delta_min:>+7,.0f}분 │ 겹침 {r.n_overlap:>3}곳{flag}")


def candidate_report(graded: pd.DataFrame, *, top: int = 20) -> str:
    """후보 요약 문장. **등급을 쓰지 않는다** — 네 가지를 후보마다 나란히 낸다.

        개선율 ± 대비 구간 │ 콜 · 동 · 총절감 │ 겹침 수 │ 표본 부족

    등급을 뺀 이유는 docs/model_flow.md 「등급은 계산하되 표시하지 않는다」 —
    244곳에서 [상] 4곳 / [하] 240곳으로 나와 표시가 판단을 돕지 못했고, [상] 4곳
    중 3곳이 표본 부족이라 최고 등급이 가장 좁은 지역을 가리켰다.
    """
    lines = []
    n = len(graded)
    lo, hi = graded["rate_pct"].min(), graded["rate_pct"].max()
    lines.append(f"  후보 {n}곳 · 개선율 {lo:+.2f} ~ {hi:+.2f}% "
                 f"(폭 {abs(hi - lo):.2f}%p) · 시드 간 σ 중앙 "
                 f"{graded['rate_sd'].median():.3f}%p")
    lines.append("  **등급은 표시하지 않는다** — 판단을 대신하는 표시라 "
                 "실무자에게 선택을 열어 두려는 도구의 성격과 어긋난다.")
    lines.append("     아래 네 가지를 직접 읽을 것. `겹침`이 클수록 그 후보의 "
                 "순위는 그만큼 말할 수 없다는 뜻이다.")
    lines.append("")

    lines.append(f"  ▸ **개선율 상위 {min(top, n)}** (정렬은 개선율, 순위표가 아니다):")
    for r in graded.head(top).itertuples():
        lines.append(_cand_line(r))

    # 최상위 후보가 몇 곳과 겹치는가 — "1위"를 말할 수 있는지의 답이다.
    best = graded.iloc[0]
    n_ov = int(best["n_overlap"])
    lines.append("")
    lines.append(f"  ▸ 개선율 1위 {best['cand_name'][:20]}({int(best['cand_id'])}) "
                 f"{best['interval']} 는 **다른 {n_ov}곳과 구간이 겹친다** — "
                 f"{'단독 1위라 말할 수 없다.' if n_ov else '겹치는 후보가 없다.'}")
    if n_ov:
        worst = graded.loc[(graded["rate_lo"] <= best["rate_hi"])
                           & (graded["rate_hi"] >= best["rate_lo"]), "rank_rate"].max()
        lines.append(f"     그 {n_ov}곳에는 개선율 {int(worst)}위까지 들어간다. "
                     "1위보다 확실히 나은 후보도, 1위가 확실히 나은 후보도 없다.")

    weak = graded.loc[~graded["sample_ok"]]
    if len(weak):
        top10 = graded.head(10)
        n_weak_top = int((~top10["sample_ok"]).sum())
        lines.append("")
        lines.append(f"  ▸ **표본 부족 {len(weak)}곳** (3km 콜 < "
                     f"{MIN_CALLS_SCOPE:,}건) — 개선율 상위 10 중 "
                     f"**{n_weak_top}곳**이 여기 걸린다:")
        for r in weak.itertuples():
            lines.append(_cand_line(r))
        lines.append("     범위가 좁아 개선율이 커 보이지만 혜택이 닿는 사람이 "
                     "그만큼 적다. 계산에서 뺀 것은 아니다.")
        # **표본 크기가 개선율 순위를 밀어 올린다** — 전체 비율과 상위권 비율을
        # 나란히 내야 그 편향이 눈에 띈다. 숫자 하나로는 "12곳뿐"으로 읽힌다.
        ok = graded["sample_ok"]
        lines.append(
            f"     전체의 {len(weak) / len(graded) * 100:.1f}% 인데 개선율 상위 "
            f"10 중 {n_weak_top}곳이다. 평균 개선율 "
            f"{graded.loc[~ok, 'rate_pct'].mean():+.2f}% vs 정상 "
            f"{graded.loc[ok, 'rate_pct'].mean():+.2f}%, 구간 폭 중앙 "
            f"{(graded.loc[~ok, 'rate_hi'] - graded.loc[~ok, 'rate_lo']).median():.2f} "
            f"vs {(graded.loc[ok, 'rate_hi'] - graded.loc[ok, 'rate_lo']).median():.2f}%p.")
        lines.append("     **개선율 순위는 표본 크기의 함수다** — 콜이 적으면 "
                     "분모가 작아 비율이 튀고 구간도 넓어진다.")

    # ── 두 번째 시선: 총 절감 분. **정렬을 갈아치우는 게 아니라 병기다.**
    lines.append("")
    lines.append("  ▸ **총 절감 분 상위 10** — 같은 결과를 수혜 규모로 본 것이다"
                 "(순위표가 아니라 대조용):")
    for r in graded.nsmallest(10, "total_delta_min").itertuples():
        lines.append(_cand_line(r))

    n = len(graded)
    top_r = set(graded.nsmallest(min(10, n), "rate_pct")["cand_id"])
    top_t = set(graded.nsmallest(min(10, n), "total_delta_min")["cand_id"])
    lines.append(f"     개선율 상위 10 과 총절감 상위 10 이 겹치는 곳: "
                 f"**{len(top_r & top_t)}곳**. "
                 f"순위 상관 {graded['rank_rate'].corr(graded['rank_total'], method='spearman'):+.2f}")
    lines.append("     **둘 중 하나로 정렬하면 반대 방향으로 오독된다** — 개선율은 "
                 "범위가 좁은 곳을, 총절감은 콜이")
    lines.append("     많은 도심을 위로 올린다. 표의 정렬이 개선율인 것은 2단계 "
                 "컷이 그 기준으로 사전 고정돼")
    lines.append("     있기 때문이지, 그쪽이 더 옳아서가 아니다.")

    lines.append("")
    lines.append("  ※ **구간이 겹치는 후보끼리는 우열을 가릴 수 없다.** 목록 순서를 "
                 "순위로 읽으면 안 된다 — 겹침 수가")
    lines.append("     그 후보를 몇 곳과 구분할 수 없는지 말해 준다.")
    lines.append(f"  ※ **대비 구간은 신뢰구간이 아니다.** 평균 ± {COMPARISON_K:.2f}σ "
                 f"로, 두 구간이 겹치지 않는 것과 {GRADE_SIGMA_K}σ 문턱이")
    lines.append("     갈리는 것이 같은 말이 되도록 맞춘 폭이다. "
                 "95% 같은 신뢰수준을 뜻하지 않는다 — σ 는 rate_sd 열에 따로 있다.")
    lines.append("  ※ **절감폭의 절대 크기는 과소평가다.** 시뮬 픽업 수준이 실측의 "
                 "53%라(A-19·A-20) 여기 나온 값도")
    lines.append("     그만큼 축소돼 있다. 실제 절감은 더 클 가능성이 높다.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────

def main(argv=None):
    # **파서를 만들기 전에** 인코딩을 잡는다 — 뒤로 미루면 `--help` 가
    # 콘솔 기본 코드페이지(cp949)로 나가 한글이 깨진다.
    sys.stdout.reconfigure(encoding="utf-8")

    ap = argparse.ArgumentParser(description="배치안 후보 평가(3km 픽업 개선율)")
    ap.add_argument("--stage", type=int, choices=(1, 2), default=None,
                    help="한 단계만 실행. 기본은 1 → 2 연속")
    ap.add_argument("--grade", action="store_true",
                    help="재생하지 않고 이미 있는 결과로 집계표만 다시 낸다")
    ap.add_argument("--seeds", type=str, default=None,
                    help="쉼표로 구분한 시드 목록(예: 42,43). 나눠 돌릴 때 쓴다. "
                         "생략하면 1단계 42 · 2단계 42,43,44")
    ap.add_argument("--top", type=int, default=STAGE2_TOP_N,
                    help=f"2단계 대상 수(개선율 상위 N). 기본 {STAGE2_TOP_N}")
    ap.add_argument("--all", action="store_true",
                    help="2단계를 **후보 전부**로 돌린다(컷 없음). --top 을 무시한다")
    ap.add_argument("--limit", type=int, default=None,
                    help="후보 수 제한(시험용)")
    args = ap.parse_args(argv)

    if args.grade:
        graded = assign_grades(load_results())
        report_frame(graded).to_csv(GRADE_PATH, index=False, encoding="utf-8-sig")
        print(f"[집계] 후보 {len(graded)}곳\n")
        print(candidate_report(graded))
        print(f"\n저장: {GRADE_PATH}")
        return

    print("입력 로딩...")
    calls = load.load_calls()
    depots = load.load_depots()
    reservation = load.load_reservation_occupancy()
    mx = S.TravelMatrix(T.TravelTime.build(),
                        set(calls["origin_dong_canon"].astype(str))
                        | set(calls["dest_dong_canon"].astype(str)))
    sub = S.slice_period(calls, start=S.REPRESENTATIVE_START,
                         days=S.REPRESENTATIVE_DAYS)
    cands = candidate_table()
    if args.limit:
        cands = cands.head(args.limit)
    dong_xy = dong_coords()
    print(f"  대표 기간 {S.REPRESENTATIVE_START} +{S.REPRESENTATIVE_DAYS}일 · "
          f"콜 {len(sub):,} · 후보 {len(cands)}곳")

    kw = dict(calls=sub, depots=depots, mx=mx, reservation=reservation,
              dong_xy=dong_xy)
    picked = parse_seeds(args.seeds)

    if args.stage in (None, 1):
        run_stage(1, cands, picked or STAGE1_SEEDS, **kw)

    if args.stage in (None, 2):
        results = load_results()
        if args.all:
            short = cands["cand_id"].tolist()
            print(f"\n[2단계 대상] **후보 전부 {len(short)}곳** — 컷 없음. "
                  f"고를 일이 없으니 사후 선택 문제도 없다")
        else:
            short = stage2_shortlist(results, args.top)
            print(f"\n[2단계 대상] 1단계 개선율 상위 {len(short)}곳 "
                  f"(컷 {args.top} 는 사전 고정값)")
        run_stage(2, cands[cands["cand_id"].isin(short)],
                  picked or STAGE2_SEEDS, **kw)

        # 시드를 다 돌리기 전에는 집계표를 내지 않는다 — 반복이 덜 쌓인 σ 로
        # 대비 구간을 그리면 나중에 시드를 채웠을 때 겹침 관계가 달라진다.
        results = load_results()
        missing = seeds_missing(results, short, STAGE2_SEEDS)
        if missing:
            print(f"\n[집계 보류] 시드 {missing} 가 아직 없다. 다 돌린 뒤 "
                  f"`--grade` 로 낼 것 — 반복이 덜 쌓인 σ 로는 구간이 흔들린다.")
            print(f"저장: {RESULT_PATH}")
            return

        graded = assign_grades(results)
        report_frame(graded).to_csv(GRADE_PATH, index=False, encoding="utf-8-sig")
        print(f"\n[집계] 후보 {len(graded)}곳\n")
        print(candidate_report(graded))
        print(f"\n저장: {RESULT_PATH} / {GRADE_PATH}")


if __name__ == "__main__":
    main()
