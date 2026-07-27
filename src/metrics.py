"""
metrics.py — 성과지표 계산

시뮬 로그(콜별 대기시간 등)에서 관문 C 채점표 지표를 산출.
효율 / 형평 / 비용 세 축.
"""
import numpy as np
import pandas as pd

# 관문 C 캘리브레이션 타깃(현행 배치 재현 목표)
#
# 분모가 지표마다 다르다 — 대조할 때 헷갈리기 쉬우니 명시해 둔다.
#   대기시간 4개 : 승차 완료 건 1,323,620 (취소 건은 wait=NaN 이라 자동 제외)
#   취소율       : 시뮬 입력 모수 1,527,213 (즉시콜+서울 전체)
#   지역 지니    : 100건 이상 동 430개 (D-04), 동 균등가중
#
# cancel_ratio 는 원래 0.142 였으나 그 값은 필터 이전 원본 전체(1,729,476)를
# 분모로 쓴 것이라 다른 지표와 모수가 어긋났다. 시뮬 입력 모수 기준 13.3% 로 정정.
TARGETS = {
    "mean_wait": 39.3,
    "median_wait": 30.8,
    "p90_wait": 77.2,
    "long_wait_ratio": 0.179,   # 60분 초과
    "cancel_ratio": 0.133,      # 정정(구 0.142) — 위 주석 참조
    "gini_dong": 0.095,
}

# D-04: 콜수가 적은 동은 평균이 크게 흔들려 지니를 왜곡한다. 100건 미만 제외.
# 원본 분석도 같은 기준이라 432개 동 중 2개(창신제3동 95건, 반포본동 24건)가 빠진다.
MIN_CALLS_PER_DONG = 100

# 장기대기 판정 기준(분)
LONG_WAIT_MIN = 60


def efficiency(log: pd.DataFrame) -> dict:
    """효율: 평균 대기, 픽업거리, 공차율."""
    raise NotImplementedError


def equity(log: pd.DataFrame) -> dict:
    """형평: 동간 대기 격차(지니), 3km 커버, 장기대기(60분 초과) 비율.

    지니는 dong_wait_table() + gini() 조합으로 계산한다(D-04 적용, 동 균등가중).
    3km 커버는 거점 좌표가 필요해 log 만으로는 안 나온다 — 배치안과 함께 계산.
    """
    raise NotImplementedError


def dong_wait_table(log: pd.DataFrame, *, min_calls: int = MIN_CALLS_PER_DONG,
                    gu_col: str = "origin_gu", dong_col: str = "origin_dong",
                    wait_col: str = "wait_min") -> pd.DataFrame:
    """동별 평균대기·콜수 집계표. 콜수 min_calls 미만 동은 제외(D-04).

    취소 건은 wait 이 NaN 이라 평균에서 빠지지만 콜수에는 잡히면 안 된다.
    여기서 말하는 '건수'는 대기시간이 산출된 건(=승차 완료) 기준이다.
    원본 분석의 동별 집계와 같은 정의라 건수가 동별로 일치한다.

    제외 내역은 attrs['n_dong_excluded'] / attrs['excluded'] 에 남긴다.
    """
    served = log[log[wait_col].notna()]
    tbl = (served.groupby([gu_col, dong_col], observed=True)[wait_col]
                 .agg(n_calls="size", mean_wait="mean")
                 .reset_index()
                 .rename(columns={gu_col: "gu", dong_col: "dong"}))

    keep = tbl["n_calls"] >= min_calls
    out = tbl[keep].sort_values(["gu", "dong"]).reset_index(drop=True)
    out.attrs["n_dong_excluded"] = int((~keep).sum())
    out.attrs["excluded"] = tbl[~keep].sort_values("n_calls").reset_index(drop=True)
    out.attrs["min_calls"] = min_calls
    return out


def dong_gini(log: pd.DataFrame, *, min_calls: int = MIN_CALLS_PER_DONG,
              weighted: bool = False, **kw) -> float:
    """동간 대기시간 지니(D-04 적용, **동 균등가중**). 관문 C 타깃 0.095.

    형평을 '동 간 격차'로 정의하므로 각 동을 한 표로 세는 균등가중이 정본이다.
    콜수로 가중하면 콜이 많은 동이 지표를 지배해 정작 소외된 동의 격차가 묻힌다.
    원본 산출물(서울즉시콜_동별_대기격차.csv)로 역산한 타깃 0.095 도 균등가중 값
    (0.0947)이라 정의와 실측이 맞아떨어진다.

    weighted=True 로 콜수 가중(0.1013)도 뽑을 수 있게 남겨뒀다 — 참고용이며
    관문 C 채점에는 쓰지 않는다. ASSA D-04 문구에 '콜수 가중'이라 적힌 것은
    오기이고, 여기 정의가 정본이다.
    """
    tbl = dong_wait_table(log, min_calls=min_calls, **kw)
    return gini(tbl["mean_wait"], weights=tbl["n_calls"] if weighted else None)


def cost(placement: pd.DataFrame) -> dict:
    """비용: 배치안 연간 사용료(3시나리오: 무상/감면50/전액) + 개선단위당 비용."""
    raise NotImplementedError


def gini(values, weights=None) -> float:
    """지니계수(가중치 선택 가능). 0 = 완전균등, 1 = 완전불균등.

    정렬 후 누적합으로 계산한다(쌍별 차이 이중루프는 430개 동에도 느리다).
      G = 1 − Σ wᵢ(Sᵢ₋₁ + Sᵢ) / (W · Sₙ),  S = 누적 Σ w·x, W = Σ w

    가중치가 없으면 균등가중. NaN·비양수 가중치 행은 버린다.
    """
    v = np.asarray(values, dtype=float)
    w = np.ones_like(v) if weights is None else np.asarray(weights, dtype=float)
    if v.shape != w.shape:
        raise ValueError(f"values{v.shape} 와 weights{w.shape} 길이가 다르다")

    keep = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[keep], w[keep]
    if v.size == 0:
        return float("nan")
    if (v < 0).any():
        raise ValueError("음수 값에는 지니를 정의하지 않는다(대기시간은 0 이상)")

    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]

    cum = np.cumsum(w * v)
    total = cum[-1]
    if total <= 0:            # 전부 0 이면 완전균등
        return 0.0
    prev = np.concatenate(([0.0], cum[:-1]))
    return float(1 - np.sum(w * (prev + cum)) / (w.sum() * total))


def compare_to_target(result: dict) -> pd.DataFrame:
    """관문 C: 산출값 vs 타깃 대조표."""
    raise NotImplementedError
