"""
metrics.py — 동별 진단 지표 계산

콜 데이터에서 동별 진단 지표(대기 · 차내 · 미이행 · 수요 · 공급 · 형평)를 뽑는다.
진단 대시보드가 읽는 재료이자, 시뮬 before/after 비교에 그대로 재사용하는 계산부다.

**지표 함수는 파일을 읽지 않는다.** 전부 데이터프레임을 인자로 받아 계산만 한다.
실측 콜(진단)이든 시뮬 로그(예측)든 같은 함수를 통과시키려는 것이다. 로딩은 load.py
담당이고, 이 모듈에서 경로를 여는 곳은 __main__ 하나뿐이다.

    import load, metrics
    calls = load.load_calls()
    tbl = metrics.build_dong_table(calls, depots=load.load_depots(),
                                   population=load.load_disabled_population())

동 키는 (구, 표준형 동명) 이다 — load.canon_dong 규칙을 그대로 쓴다. 콜 원본의
동 구분은 432개로 경계 파일(426)·장애인 통계(427)와 행정구역 판이 서로 달라
어느 쪽도 완전히 겹치지 않는다. 조인 실패는 버리지 않고 NaN + 플래그로 남기고
건수를 attrs 에 적는다. 자세한 내역은 docs/calibration.md 참조.

산출·검증 출력: python src/metrics.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from travel_time import RIDE_MIN_RANGE, SPEED_RANGE, haversine_km

# ─────────────────────────────────────────────────────────────
# 검증 기준 · 상수
# ─────────────────────────────────────────────────────────────

# 검증 기준(현행 배치 재현 목표)
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

# 판정 허용오차. 대기시간은 분, 비율은 절대비율.
TARGET_TOL = {
    "mean_wait": 0.3, "median_wait": 0.3, "p90_wait": 0.5,
    "long_wait_ratio": 0.005, "cancel_ratio": 0.002, "gini_dong": 0.002,
}

# D-04: 콜수가 적은 동은 평균이 크게 흔들려 지니를 왜곡한다. 100건 미만 제외.
# 원본 분석도 같은 기준이라 432개 동 중 2개(창신제3동 95건, 반포본동 24건)가 빠진다.
# 동별 표에서는 버리지 않고 is_reliable=False 로 표시만 한다.
MIN_CALLS_PER_DONG = 100

# 장기대기 판정 기준(분)
LONG_WAIT_MIN = 60

# 즉시 취소 판정(분) — 배차 전 취소 중 이 시간 안에 끊은 건은 변심으로 본다.
CANCEL_IMMEDIATE_MIN = 1.0

# 공급 근접도 반경(km)
SUPPLY_RADIUS_KM = 3.0

# 동 키 — 콜 데이터 컬럼명
DONG_KEYS = ("origin_gu", "origin_dong_canon")

# 대기 3구간. 배차가 늦은 건지(매칭) 차가 멀리 있는 건지(픽업) 갈라 보려는 것이다.
#   매칭 = 배차 − 접수 / 픽업 = 승차 − 배차 / 총 = 승차 − 접수
WAIT_KINDS = ("match", "pickup", "total")

# 미이행 4구간. abandoned 가 공급 부족의 핵심 신호다.
CANCEL_KINDS = ("immediate", "abandoned", "after_assign", "other")

# 표·대시보드 표기용 이름. travel_time.PERIOD_KO 와 같은 용도.
CANCEL_KIND_KO = {
    "immediate": "즉시 취소", "abandoned": "대기 중 포기",
    "after_assign": "배차 후 취소", "other": "기타(불명)",
}

# load.py 의 4구간 시간대(심야/아침/낮/저녁) → 컬럼명용 ASCII 키.
# travel_time 의 5구간과는 기준이 다르다. 섞어 쓰지 말 것.
PERIOD_EN = {"심야": "night", "아침": "morning", "낮": "day", "저녁": "evening"}

# 구/서울 평균 대비를 붙일 지표(기본)
COMPARE_COLS = ("wait_total_mean", "long_wait_ratio", "cancel_abandoned_ratio",
                "usage_rate", "vehicles_3km")


# ─────────────────────────────────────────────────────────────
# 콜 단위 파생 — 원본을 건드리지 않고 Series 로 돌려준다
# ─────────────────────────────────────────────────────────────

def pickup_min(calls: pd.DataFrame) -> pd.Series:
    """픽업 대기(분) = 승차 − 배차. 차가 얼마나 멀리 있었나."""
    return (calls["boarded_at"] - calls["assigned_at"]).dt.total_seconds() / 60


def ride_km(calls: pd.DataFrame) -> pd.Series:
    """승차거리(km). 원본이 미터라 나눈다."""
    return calls["ride_distance_m"] / 1000


def ride_kmh(calls: pd.DataFrame) -> pd.Series:
    """차내 평균속도(km/h) = 승차거리 ÷ 소요시간."""
    return ride_km(calls) / (calls["ride_min"] / 60)


def ride_valid(calls: pd.DataFrame) -> pd.Series:
    """차내 지표에 쓸 만한 운행인지. travel_time 의 이상치 컷과 같은 기준이다.

    운행시간 1~120분 · 거리 > 0 · 속도 2~60km/h. 하차 미기록(86건)과
    거리 0(대기시간만 남고 운행이 없는 건)이 여기서 걸린다.
    """
    return (calls["ride_min"].between(*RIDE_MIN_RANGE)
            & (calls["ride_distance_m"] > 0)
            & ride_kmh(calls).between(*SPEED_RANGE))


def cancel_kind(calls: pd.DataFrame) -> pd.Series:
    """미이행 4구간 분류. 승차한 건은 NaN.

      immediate    배차 전 + 접수 후 1분 이내 취소 — 오입력·변심
      abandoned    배차 전 + 1분 초과 — **기다리다 포기. 공급 부족의 핵심 신호**
      after_assign 배차는 됐는데 승차 기록이 없음 — 차가 오다 어긋난 건
      other        배차 전 취소 중 취소 시각이 결측이라 즉시/포기를 판별할 수 없는
                   건(24건). 근거 없는 분류를 피해 별도 구간으로 둔다.

    other 를 3구간 중 하나에 흡수하지 않는 이유: 배차 전이라 '배차 후 취소'가 아니고,
    시각을 모르니 '즉시'·'포기' 어느 쪽으로 몰아도 근거가 없다. 특히 '대기 중 포기'는
    공급 부족 신호로 읽는 값이라 오염되면 안 된다. 네 구간의 합이 전체 취소와 정확히
    맞아야 취소율도 검증 기준 13.3%와 어긋나지 않는다.

    취소 시각이 접수보다 앞선 기록이 4건 있는데 immediate 로 들어간다(오차 무시).
    """
    canceled = calls["boarded_at"].isna()
    assigned = calls["assigned_at"].notna()
    # 취소가 한 건도 없는 로그(시뮬 결과 등)는 canceled_at 이 전부 결측이라 datetime
    # 으로 안 잡힌다. 실측 datetime64 에는 사실상 무비용이라 그냥 한 번 통과시킨다.
    canceled_at = pd.to_datetime(calls["canceled_at"], errors="coerce")
    delay = (canceled_at - calls["received_at"]).dt.total_seconds() / 60

    kind = pd.Series(np.nan, index=calls.index, dtype=object)
    pre = canceled & ~assigned
    kind[canceled & assigned] = "after_assign"
    kind[pre & (delay <= CANCEL_IMMEDIATE_MIN)] = "immediate"
    kind[pre & (delay > CANCEL_IMMEDIATE_MIN)] = "abandoned"
    kind[pre & delay.isna()] = "other"
    return pd.Categorical(kind, categories=CANCEL_KINDS)


# ─────────────────────────────────────────────────────────────
# 동별 집계
# ─────────────────────────────────────────────────────────────

def _by_dong(df: pd.DataFrame, keys=DONG_KEYS):
    return df.groupby(list(keys), observed=True)


def _tidy(out: pd.DataFrame) -> pd.DataFrame:
    """집계 결과의 동 키를 표 컬럼으로 되돌린다. category 는 str 로 풀어 조인에 쓴다."""
    out = out.reset_index().rename(columns={
        "origin_gu": "gu", "origin_dong_canon": "dong_canon", "origin_dong": "dong"})
    for col in ("gu", "dong_canon", "dong"):
        if col in out.columns:
            out[col] = out[col].astype(str)
    return out


def _stats(grouped, col: str, prefix: str) -> pd.DataFrame:
    """그룹별 평균 · 중앙(p50) · p90 세 컬럼."""
    g = grouped[col]
    out = g.agg(**{f"{prefix}_mean": "mean", f"{prefix}_p50": "median"})
    out[f"{prefix}_p90"] = g.quantile(0.90)
    return out


def dong_wait(calls: pd.DataFrame, *, keys=DONG_KEYS) -> pd.DataFrame:
    """동별 대기 지표. 매칭/픽업/총 각각 평균·p50·p90 + 장기대기율.

    분모는 대기시간이 산출된 건(=승차 완료)이다. 취소 건은 대기시간이 NaN 이라
    평균에서 빠지고 n_served 에도 잡히지 않는다 — 원본 분석의 동별 집계와 같은 정의다.

    매칭·픽업은 배차 기록이 있는 건만이라 총 대기보다 분모가 작다(n_assigned).
    """
    df = calls.assign(pickup_min=pickup_min(calls),
                      is_long=calls["wait_min"] > LONG_WAIT_MIN)
    served = df[df["wait_min"].notna()]

    g = _by_dong(served, keys)
    out = _stats(g, "wait_min", "wait_total")
    out["n_served"] = g.size()
    out["long_wait_ratio"] = g["is_long"].mean()

    for kind, col in (("match", "assign_min"), ("pickup", "pickup_min")):
        sub = served[served[col].notna()]
        out = out.join(_stats(_by_dong(sub, keys), col, f"wait_{kind}"))
    out["n_assigned"] = _by_dong(served[served["assign_min"].notna()], keys).size()

    return _tidy(out)


def dong_ride(calls: pd.DataFrame, *, keys=DONG_KEYS) -> pd.DataFrame:
    """동별 차내 지표. 소요시간(분) · 속도(km/h) · 거리(km) 평균.

    이상치를 뺀 정상 운행만 쓴다(ride_valid). 분모는 n_ride 로 따로 남긴다 —
    n_served 와 다르다.
    """
    df = calls.assign(ride_km=ride_km(calls), ride_kmh=ride_kmh(calls))
    ok = df[ride_valid(calls)]

    g = _by_dong(ok, keys)
    out = g.agg(n_ride=("ride_min", "size"),
                ride_min_mean=("ride_min", "mean"),
                ride_kmh_mean=("ride_kmh", "mean"),
                ride_km_mean=("ride_km", "mean"))
    return _tidy(out)


def dong_cancel(calls: pd.DataFrame, *, keys=DONG_KEYS) -> pd.DataFrame:
    """동별 미이행 4구간 비율. 분모는 전체 접수 콜(취소 포함).

    즉시 취소 / 대기 중 포기 / 배차 후 취소 / 기타(불명) — cancel_{종류}_ratio
    4개의 합 = cancel_ratio 로 정확히 맞는다.
    """
    df = calls.assign(cancel_kind=cancel_kind(calls))
    n = _by_dong(df, keys).size().rename("n_calls")

    counts = (_by_dong(df, keys)["cancel_kind"]
              .value_counts().unstack(fill_value=0)
              .reindex(columns=list(CANCEL_KINDS), fill_value=0))

    out = counts.div(n, axis=0).add_prefix("cancel_").add_suffix("_ratio")
    out["cancel_ratio"] = out.sum(axis=1)
    return _tidy(out)


def dong_demand(calls: pd.DataFrame, *, keys=DONG_KEYS) -> pd.DataFrame:
    """동별 수요. 접수 콜 수 + 시간대(4구간)·평일/주말 분해 + 동 중심좌표.

    좌표는 load.load_calls 가 행마다 붙여둔 출발동 중심점이다(동 안에서 같은 값).
    """
    df = calls.assign(is_weekend=calls["weekday"] >= 5)
    g = _by_dong(df, keys)

    out = g.agg(n_calls=("call_id", "size"),
                lat=("origin_lat", "first"), lon=("origin_lon", "first"),
                dong=("origin_dong", "first"))

    period = (g["period"].value_counts().unstack(fill_value=0)
              .rename(columns=PERIOD_EN).add_prefix("n_calls_"))
    weekend = g["is_weekend"].sum()
    week = pd.DataFrame({"n_calls_weekday": out["n_calls"] - weekend,
                         "n_calls_weekend": weekend})

    return _tidy(out.join(period).join(week))


def demand_matrix(calls: pd.DataFrame, *, keys=DONG_KEYS) -> pd.DataFrame:
    """동 × 시간대 × 평일/주말 콜 수 매트릭스(long form).

    동별 표에 접는 대신 원형을 남긴다 — 대시보드에서 시간대 슬라이더를 걸거나
    시뮬 도착 프로세스를 동별로 맞출 때 쓴다.

    반환 컬럼: gu, dong_canon, period, is_weekend, n_calls
    """
    df = calls.assign(is_weekend=calls["weekday"] >= 5)
    out = (df.groupby(list(keys) + ["period", "is_weekend"], observed=True)
             .size().rename("n_calls").reset_index())
    out = out.rename(columns={"origin_gu": "gu", "origin_dong_canon": "dong_canon"})
    for col in ("gu", "dong_canon", "period"):
        out[col] = out[col].astype(str)
    return out.sort_values(["gu", "dong_canon", "period", "is_weekend"]).reset_index(drop=True)


def dong_supply(dong_xy: pd.DataFrame, depots: pd.DataFrame, *,
                radius_km: float = SUPPLY_RADIUS_KM) -> pd.DataFrame:
    """동 중심점 반경 안의 차고지 배속 차량 합(3km 근접도).

    haversine 직선거리다. 실제 접근은 도로를 타므로 낙관적인 값이고, 반경 경계에
    걸친 차고지는 들어오거나 빠지거나로 갈린다(계단 함수). 432 × 44 라 전수 계산한다.

    dong_xy: gu, dong_canon, lat, lon / depots: lat, lon, capacity
    반환: gu, dong_canon, vehicles_3km, n_depots_3km
    """
    d = haversine_km(dong_xy["lat"].to_numpy()[:, None], dong_xy["lon"].to_numpy()[:, None],
                     depots["lat"].to_numpy()[None, :], depots["lon"].to_numpy()[None, :])
    within = d <= radius_km

    out = dong_xy[["gu", "dong_canon"]].copy()
    out["vehicles_3km"] = within @ depots["capacity"].to_numpy()
    out["n_depots_3km"] = within.sum(axis=1)
    return out


def dong_population(dong_keys: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    """동별 등록 장애인 수를 콜 데이터의 동 구분에 맞춰 붙인다.

    두 파일의 행정구역 판이 달라 표준형만으로는 432개 중 18개가 안 맞는다. 콜 쪽이
    더 잘게 쪼개진 경우(중구 신당1~6동 ↔ 통계 신당동)와 더 뭉뚱그린 경우(강동구
    상일동 ↔ 통계 상일1·2동)가 섞여 있다.

    표준형으로 먼저 맞추고, 남은 건만 기본명(숫자 뗀 이름) 그룹으로 재시도한다.
    단 **기본명 그룹을 두 개 이상의 동이 다투면 붙이지 않는다** — 신당1~6동처럼
    콜 쪽이 더 잘면 통계 한 줄을 어떻게 나눌지 근거가 없어서다. 이 규칙으로
    상일동(10,849콜)·공릉13동(7,725콜)이 살아나고 나머지 16개는 NaN 으로 남는다.

    이미 표준형으로 붙은 통계 행은 기본명 후보에서 뺀다(공릉2동 이중 계상 방지).

    반환: gu, dong_canon, n_disabled, pop_match ('canon' / 'base' / NaN)
    """
    from load import _base_dong  # 동명 정규화 규칙은 load.py 한 곳에서만 정의한다

    out = dong_keys[["gu", "dong_canon"]].copy()
    exact = population.set_index(["gu", "dong_canon"])["n_disabled"]
    out["n_disabled"] = pd.MultiIndex.from_frame(out[["gu", "dong_canon"]]).map(exact)
    out["pop_match"] = np.where(out["n_disabled"].notna(), "canon", None)

    todo = out.loc[out["n_disabled"].isna(), ["gu", "dong_canon"]].copy()
    if todo.empty:
        return out

    todo["base"] = todo["dong_canon"].map(_base_dong)
    todo["n_claim"] = todo.groupby(["gu", "base"])["dong_canon"].transform("size")

    matched = set(zip(out.loc[out["pop_match"] == "canon", "gu"],
                      out.loc[out["pop_match"] == "canon", "dong_canon"]))
    free = population[~pd.Series(list(zip(population["gu"], population["dong_canon"])),
                                 index=population.index).isin(matched)]
    # min_count=1 — 통계값이 '-'(NaN)뿐인 그룹까지 0명으로 만들면 안 된다(반포본동).
    group_sum = free.groupby(["gu", "dong_base"])["n_disabled"].sum(min_count=1)

    solo = todo[todo["n_claim"] == 1]
    vals = pd.MultiIndex.from_frame(solo[["gu", "base"]]).map(group_sum)
    out.loc[solo.index, "n_disabled"] = vals
    out.loc[solo.index, "pop_match"] = np.where(pd.notna(vals), "base", None)

    return out


# ─────────────────────────────────────────────────────────────
# 비교(구내 / 타구 / 서울)
# ─────────────────────────────────────────────────────────────

def add_comparisons(tbl: pd.DataFrame, cols=COMPARE_COLS, *,
                    reliable_baseline: bool = True) -> pd.DataFrame:
    """각 동 값에 '구 평균 대비 / 서울 평균 대비'를 붙인다.

    기준선은 **동 균등가중 평균**이다(콜수 가중 아님) — 형평을 동 간 격차로 보는
    지니와 관점을 맞춘다. 표본이 얇은 동이 기준선을 흔들지 않도록 기본은 신뢰 동
    (100건 이상)만으로 평균을 내되, 비율 자체는 모든 동에 계산한다.

    붙는 컬럼: {col}_gu_mean, {col}_seoul_mean, {col}_vs_gu, {col}_vs_seoul
    (vs_* 는 배수다. 1.2 면 기준선보다 20% 높다는 뜻.)

    없는 컬럼을 넘기면 바로 KeyError 다. 조용히 건너뛰면 오타 하나에 비교 컬럼이
    통째로 빠진 표가 멀쩡한 얼굴로 나온다.
    """
    missing = [c for c in cols if c not in tbl.columns]
    if missing:
        raise KeyError(f"비교할 컬럼이 표에 없다: {missing}")

    out = tbl.copy()
    base = out[out["is_reliable"]] if reliable_baseline and "is_reliable" in out else out

    for col in cols:
        gu_mean = out["gu"].map(base.groupby("gu")[col].mean())
        seoul_mean = base[col].mean()
        out[f"{col}_gu_mean"] = gu_mean
        out[f"{col}_seoul_mean"] = seoul_mean
        out[f"{col}_vs_gu"] = out[col] / gu_mean.replace(0, np.nan)
        out[f"{col}_vs_seoul"] = out[col] / (seoul_mean or np.nan)
    return out


# ─────────────────────────────────────────────────────────────
# 동별 표 조립
# ─────────────────────────────────────────────────────────────

# 표 컬럼 순서 — 식별 → 수요 → 대기 → 차내 → 미이행 → 공급 → 인구.
# 비교 컬럼(_vs_gu 등)은 여기 없는 것들과 함께 뒤에 원래 순서대로 붙는다.
_COLUMN_ORDER = (
    "gu", "dong", "dong_canon", "lat", "lon", "is_reliable",
    "n_calls", "n_served", "n_assigned", "n_ride",
    "n_calls_night", "n_calls_morning", "n_calls_day", "n_calls_evening",
    "n_calls_weekday", "n_calls_weekend",
    "wait_total_mean", "wait_total_p50", "wait_total_p90", "long_wait_ratio",
    "wait_match_mean", "wait_match_p50", "wait_match_p90",
    "wait_pickup_mean", "wait_pickup_p50", "wait_pickup_p90",
    "ride_min_mean", "ride_kmh_mean", "ride_km_mean",
    "cancel_ratio", "cancel_immediate_ratio", "cancel_abandoned_ratio",
    "cancel_after_assign_ratio", "cancel_other_ratio",
    "vehicles_3km", "n_depots_3km",
    "n_disabled", "pop_match", "usage_rate",
)


def _order_columns(tbl: pd.DataFrame) -> pd.DataFrame:
    head = [c for c in _COLUMN_ORDER if c in tbl.columns]
    return tbl[head + [c for c in tbl.columns if c not in head]]


def build_dong_table(calls: pd.DataFrame, *, depots: pd.DataFrame = None,
                     population: pd.DataFrame = None,
                     min_calls: int = MIN_CALLS_PER_DONG,
                     compare_cols=COMPARE_COLS) -> pd.DataFrame:
    """동별 진단 지표 표. 대시보드가 읽는 산출물이자 시뮬 before/after 의 한 쪽.

    depots / population 을 넘기지 않으면 공급·이용률 컬럼만 빠지고 나머지는 그대로다
    (시뮬 로그처럼 콜만 있는 입력에도 쓸 수 있게).

    콜이 한 건이라도 있는 동은 전부 남기고, 100건 미만은 is_reliable=False 로만
    표시한다 — 지도에서 지워버리면 '데이터가 없는 동'과 구분이 안 된다.

    attrs: n_dong, n_reliable, n_pop_unmatched, pop_unmatched(동 목록)
    """
    tbl = dong_demand(calls)
    for part in (dong_wait(calls), dong_ride(calls), dong_cancel(calls)):
        tbl = tbl.merge(part, on=["gu", "dong_canon"], how="left")

    # 신뢰 기준은 지니(D-04)와 같은 분모로 잰다 — 승차 완료 건이지 접수 건이 아니다.
    # 접수 기준으로 재면 창신제3동(접수 108 / 승차 95)처럼 지니에서는 빠진 동이
    # 표에서는 신뢰로 잡혀 두 지표의 모집단이 어긋난다.
    tbl["is_reliable"] = tbl["n_served"].fillna(0) >= min_calls

    if depots is not None:
        tbl = tbl.merge(dong_supply(tbl[["gu", "dong_canon", "lat", "lon"]], depots),
                        on=["gu", "dong_canon"], how="left")

    unmatched = []
    if population is not None:
        pop = dong_population(tbl[["gu", "dong_canon"]], population)
        tbl = tbl.merge(pop, on=["gu", "dong_canon"], how="left")
        # 이용률 = 연간 접수 콜 ÷ 등록 장애인. 1인당 몇 번 불렀나.
        tbl["usage_rate"] = tbl["n_calls"] / tbl["n_disabled"].replace(0, np.nan)
        unmatched = sorted(tbl.loc[tbl["n_disabled"].isna(), "dong_canon"])

    # depots/population 을 안 넘기면 그쪽 비교는 자연히 빠진다. 그 외의 이름은
    # add_comparisons 가 오타로 보고 튕긴다.
    tbl = add_comparisons(tbl, [c for c in compare_cols if c in tbl.columns])
    tbl = _order_columns(tbl)
    tbl = tbl.sort_values(["gu", "dong_canon"]).reset_index(drop=True)

    # attrs 는 merge/sort 를 거치며 사라질 수 있어 마지막에 한 번에 붙인다.
    tbl.attrs["n_dong"] = len(tbl)
    tbl.attrs["n_reliable"] = int(tbl["is_reliable"].sum())
    tbl.attrs["min_calls"] = min_calls
    tbl.attrs["n_pop_unmatched"] = len(unmatched)
    tbl.attrs["pop_unmatched"] = unmatched
    return tbl


# ─────────────────────────────────────────────────────────────
# 차량 생산성 (동별 아님 — 차량 단위 요약)
# ─────────────────────────────────────────────────────────────

def vehicle_day_table(trips: pd.DataFrame) -> pd.DataFrame:
    """차량-일 단위 집계. trips 는 load.load_vehicle_trips() 형식.

    가동시간은 그날 첫 승차 ~ 마지막 하차 구간(span)으로 잡는다. 배차를 기다리며
    서 있던 시간은 운행 기록에 안 남아 이 방법 말고는 알 길이 없다.

    반환 컬럼: vehicle_id, service_date, trips, occupied_min, span_h
    """
    g = trips.groupby(["vehicle_id", "service_date"])
    out = g.agg(trips=("ride_min", "size"), occupied_min=("ride_min", "sum"),
                first_board=("boarded_at", "min"), last_alight=("alighted_at", "max"))
    out["span_h"] = (out["last_alight"] - out["first_board"]).dt.total_seconds() / 3600
    return out.reset_index()[["vehicle_id", "service_date", "trips",
                              "occupied_min", "span_h"]]


def vehicle_productivity(trips: pd.DataFrame, *, min_span_h: float = 0.5) -> dict:
    """차량 생산성 요약(전체 차량 평균).

      trips_per_day    차량 하루 처리 콜 수
      trips_per_hour   가동 1시간당 처리 콜 수
      occupied_ratio   실차율 = 실차 시간(승차~하차) ÷ 가동 시간

    ⚠ **실차율은 상한으로 읽어야 한다.** 가동시간을 첫 승차~마지막 하차로 잡아서
    출근 후 첫 콜을 받기까지, 마지막 하차 뒤 퇴근까지의 유휴가 분모에서 빠진다.
    반대로 기사 휴게는 분모에 그대로 들어가 값을 낮추는 쪽으로 작용하는데, 둘을
    가를 근거가 원본에 없다. 정확한 가동시간은 배차 로그가 있어야 나온다.

    차량 수는 1년 누계라 특정 시점 가동 대수가 아니다(load_vehicle_trips 참조).
    운행이 1건뿐인 날은 span 이 0에 가까워 시간당 통행이 발산한다 — min_span_h
    미만인 차량-일은 비율 계산에서 뺀다(건수는 n_vehicle_days_dropped 로 남긴다).
    """
    vd = vehicle_day_table(trips)
    ok = vd[vd["span_h"] >= min_span_h]

    return {
        "n_vehicles": int(vd["vehicle_id"].nunique()),
        "n_vehicle_days": int(len(vd)),
        "n_trips": int(vd["trips"].sum()),
        "trips_per_day": float(vd["trips"].mean()),
        "trips_per_hour": float((ok["trips"] / ok["span_h"]).mean()),
        "occupied_ratio": float((ok["occupied_min"] / 60 / ok["span_h"]).mean()),
        "n_vehicle_days_dropped": int(len(vd) - len(ok)),
    }


# ─────────────────────────────────────────────────────────────
# 형평
# ─────────────────────────────────────────────────────────────

def dong_wait_table(log: pd.DataFrame, *, min_calls: int = MIN_CALLS_PER_DONG,
                    gu_col: str = "origin_gu", dong_col: str = "origin_dong",
                    wait_col: str = "wait_min") -> pd.DataFrame:
    """동별 평균대기·콜수 집계표. 콜수 min_calls 미만 동은 제외.

    지니 전용의 최소 집계다 — 동별 표(build_dong_table)와 달리 원본 분석과 대조할
    수 있게 동명을 원본 표기 그대로 두고, 기준 미만 동은 아예 뺀다.

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
    """동간 대기시간 지니(**동 균등가중**). 검증 기준 0.095.

    형평을 '동 간 격차'로 정의하므로 각 동을 한 표로 세는 균등가중이 정본이다.
    콜수로 가중하면 콜이 많은 동이 지표를 지배해 정작 소외된 동의 격차가 묻힌다.
    원본 산출물(서울즉시콜_동별_대기격차.csv)로 역산한 타깃 0.095 도 균등가중 값
    (0.0947)이라 정의와 실측이 맞아떨어진다.

    weighted=True 로 콜수 가중(0.1013)도 뽑을 수 있게 남겨뒀다 — 참고용이며
    검증 채점에는 쓰지 않는다.
    """
    tbl = dong_wait_table(log, min_calls=min_calls, **kw)
    return gini(tbl["mean_wait"], weights=tbl["n_calls"] if weighted else None)


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


def standard_compliance(tbl: pd.DataFrame) -> pd.DataFrame:
    """기준 충족률 — 동별 값이 서비스 기준선을 넘는 비율.

    아직 비워둔다. 기준선(예: '평균 대기 30분 이하')을 먼저 정해야 하는데, 분포를
    보고 정하기로 했다. build_dong_table 의 wait_total_p50/p90 분포를 확인한 뒤 채운다.
    """
    raise NotImplementedError("기준선 미정 — 동별 대기 분포 확인 후 결정")


# ─────────────────────────────────────────────────────────────
# 검증
# ─────────────────────────────────────────────────────────────

def overall_metrics(calls: pd.DataFrame) -> dict:
    """전체(서울 합산) 지표. 검증 기준과 같은 6개, 같은 분모.

    대기 4개는 승차 완료 건, 취소율은 접수 전체, 지니는 100건 이상 동이 분모다.
    """
    w = calls["wait_min"].dropna()
    return {
        "mean_wait": float(w.mean()),
        "median_wait": float(w.median()),
        "p90_wait": float(w.quantile(0.90)),
        "long_wait_ratio": float((w > LONG_WAIT_MIN).mean()),
        "cancel_ratio": float(calls["boarded_at"].isna().mean()),
        "gini_dong": dong_gini(calls),
    }


def compare_to_target(result: dict) -> pd.DataFrame:
    """검증: 산출값 vs 검증 기준 대조표. 허용오차는 TARGET_TOL."""
    rows = []
    for key, target in TARGETS.items():
        got = result.get(key, np.nan)
        tol = TARGET_TOL[key]
        rows.append({"지표": key, "산출": got, "기준": target,
                     "차이": got - target, "허용": tol,
                     "판정": "OK" if abs(got - target) <= tol else "X"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    import load

    OUT_DIR = Path(__file__).resolve().parent.parent / "outputs"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("콜 로딩 중(첫 실행은 1~2분, 이후 캐시)...")
    calls = load.load_calls()
    n_dong = len(calls.groupby(["origin_gu", "origin_dong_canon"], observed=True))
    print(f"콜 {len(calls):,}건 / 출발동 {n_dong}개\n")

    depots = load.load_depots()
    pop = load.load_disabled_population()
    print(f"차고지 {len(depots)}개(차량 {depots['capacity'].sum()}대) / "
          f"등록 장애인 통계 {len(pop)}개 동, {int(pop['n_disabled'].sum()):,}명\n")

    tbl = build_dong_table(calls, depots=depots, population=pop)
    matrix = demand_matrix(calls)

    print(f"동별 표         {tbl.attrs['n_dong']}개 동 × {tbl.shape[1]}개 지표  "
          f"(신뢰 {tbl.attrs['n_reliable']} / 100건 미만 "
          f"{tbl.attrs['n_dong'] - tbl.attrs['n_reliable']})")
    n_miss = tbl.attrs.get("n_pop_unmatched", 0)
    if n_miss:
        print(f"  ⚠ 장애인 통계 미매칭 {n_miss}개 동 → 이용률 NaN: "
              f"{', '.join(tbl.attrs['pop_unmatched'][:6])}"
              f"{' 외' if n_miss > 6 else ''}")
    print(f"수요 매트릭스   {len(matrix):,}행 (동 × 시간대 × 평일/주말)")

    tbl.to_csv(OUT_DIR / "dong_metrics.csv", index=False, encoding="utf-8-sig")
    tbl.to_parquet(OUT_DIR / "dong_metrics.parquet", index=False)
    matrix.to_parquet(OUT_DIR / "dong_demand_matrix.parquet", index=False)

    print("\n차량 운행 로딩 중...")
    prod = vehicle_productivity(load.load_vehicle_trips())
    pd.DataFrame([prod]).to_csv(OUT_DIR / "vehicle_productivity.csv",
                                index=False, encoding="utf-8-sig")
    print(f"차량 생산성     차량 {prod['n_vehicles']}대 / "
          f"가동일 {prod['n_vehicle_days']:,}일 / 운행 {prod['n_trips']:,}건")
    print(f"  하루 통행     {prod['trips_per_day']:.2f}건/대")
    print(f"  시간당 통행   {prod['trips_per_hour']:.2f}건/대")
    print(f"  실차율        {prod['occupied_ratio']:.1%}  (상한 — docstring 참조)")

    print(f"\n저장: {OUT_DIR}")
    print("\n검증 (전체값 vs 검증 기준)")
    print(compare_to_target(overall_metrics(calls)).to_string(index=False))
