# -*- coding: utf-8 -*-
"""step7_idle.py — 차량 유휴 구간 산출

차량이 콜에 묶이지 않고 비어 있던 구간을 실측에서 뽑는다.

정의
    유휴 구간 = 어떤 운행의 **하차일시** ~ 같은 차량의 **다음 배차일시**

    다음 '승차'일시가 아니라 다음 '배차'일시다. 배차를 받은 시점부터는 이미 다음 콜에
    배정된 상태라 공차 이동 중이지 유휴가 아니다. 이 구간이 시뮬의 '하차지 인근 제자리
    대기'(가정 A-01)에 대응한다.

입력  calltaxi_2025_merged.csv (차량번호 병합본)
필터  승차·하차 시각이 모두 기록된 완료 운행 + 배차 시각 기록 + 차량번호 있음
      (simulation/src/load.py 의 load_vehicle_trips 와 같은 기준에 배차 시각 조건을 더한 것)

산출  유휴 구간 건수 / 길이 분포(중앙·p90) / 시간대별 동시 유휴 차량 수
      simulation/outputs/idle_gaps.csv, idle_by_hour.csv

실행 (저장소 루트에서)
    .venv/Scripts/python.exe analysis/step7_idle.py

데이터 폴더는 환경변수 DATA_DIR 이 있으면 그쪽, 없으면 저장소 상위의 ../data/ 다
(load.resolve_data_dir 과 같은 규칙).
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent.parent          # simulation/
DATA_DIR = (Path(os.environ.get("DATA_DIR", "").strip()).expanduser().resolve()
            if os.environ.get("DATA_DIR", "").strip() else REPO.parent / "data")
OUT_DIR = REPO / "outputs"

SRC = DATA_DIR / "calltaxi_2025_merged.csv"
USECOLS = ["차량번호", "배차일시", "승차일시", "하차일시"]

MINUTES_PER_DAY = 1440


def load_trips(path: Path = SRC) -> pd.DataFrame:
    """운행 기록 로딩·필터. 단계별 잔존 건수는 attrs['funnel'] 에 담는다."""
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=USECOLS, low_memory=False)
    funnel = {"원본": len(df)}

    assign = pd.to_datetime(df["배차일시"], format="ISO8601", errors="coerce")
    board = pd.to_datetime(df["승차일시"], format="ISO8601", errors="coerce")
    alight = pd.to_datetime(df["하차일시"], format="ISO8601", errors="coerce")

    ok = board.notna() & alight.notna()
    assign, board, alight, df = assign[ok], board[ok], alight[ok], df[ok]
    funnel["승차·하차 기록"] = len(df)

    ok = assign.notna()
    assign, board, alight, df = assign[ok], board[ok], alight[ok], df[ok]
    funnel["배차 기록"] = len(df)

    vehicle = pd.to_numeric(df["차량번호"], errors="coerce")
    ok = vehicle.notna()
    funnel["차량번호 있음"] = int(ok.sum())

    out = pd.DataFrame({
        "vehicle_id": vehicle[ok].astype("int64").to_numpy(),
        "assigned_at": assign[ok].to_numpy(),
        "alighted_at": alight[ok].to_numpy(),
    })
    out.attrs["funnel"] = funnel
    return out.reset_index(drop=True)


def build_gaps(trips: pd.DataFrame) -> pd.DataFrame:
    """차량별 시간순 정렬 후 (하차 → 다음 배차) 구간을 만든다.

    마지막 운행은 다음 배차가 없어 구간이 만들어지지 않는다 — 차량 수만큼 줄어든다.
    반환: vehicle_id, start(하차), end(다음 배차), idle_min, is_same_day
    """
    t = trips.sort_values(["vehicle_id", "assigned_at"], kind="stable")
    nxt = t.groupby("vehicle_id", sort=False)["assigned_at"].shift(-1)

    gaps = pd.DataFrame({
        "vehicle_id": t["vehicle_id"].to_numpy(),
        "start": t["alighted_at"].to_numpy(),
        "end": nxt.to_numpy(),
    }).dropna(subset=["end"]).reset_index(drop=True)

    gaps["idle_min"] = (gaps["end"] - gaps["start"]).dt.total_seconds() / 60
    gaps["is_same_day"] = gaps["start"].dt.normalize() == gaps["end"].dt.normalize()
    return gaps


def hourly_concurrency(gaps: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """시간대(0~23)별 평균 동시 유휴 차량 수.

    구간 [start, end) 를 하루 24개 시간 칸에 잘라 넣고, 칸별 총 유휴 분을
    (60분 × 일수) 로 나눈다. 그 시간대에 평균 몇 대가 동시에 비어 있었나가 된다.

        f(t, h) = ⌊t/1440⌋ × 60 + clip(t mod 1440 − 60h, 0, 60)
        구간의 h 칸 점유 = f(end, h) − f(start, h)

    누적분포 f 의 차분이라 자정을 넘는 구간도 자동으로 갈라진다.
    """
    epoch = gaps["start"].min().normalize()
    s = (gaps["start"] - epoch).dt.total_seconds().to_numpy() / 60
    e = (gaps["end"] - epoch).dt.total_seconds().to_numpy() / 60

    def occupied(t, h):
        whole, rem = np.divmod(t, MINUTES_PER_DAY)
        return whole * 60 + np.clip(rem - 60 * h, 0, 60)

    rows = []
    for h in range(24):
        minutes = float((occupied(e, h) - occupied(s, h)).sum())
        rows.append({"hour": h, "idle_min_total": minutes,
                     "mean_idle_vehicles": minutes / (60 * n_days)})
    return pd.DataFrame(rows)


def main():
    if not SRC.exists():
        print(f"원본이 없다: {SRC}")
        print("→ 폴더 구조가 다르면 환경변수 DATA_DIR 로 지정한다.")
        raise SystemExit(1)

    print(f"원본 로딩 중: {SRC.name}")
    trips = load_trips()
    for k, v in trips.attrs["funnel"].items():
        print(f"  {k:<16} {v:>10,}")

    gaps = build_gaps(trips)
    n_vehicles = trips["vehicle_id"].nunique()
    print(f"\n유휴 구간 {len(gaps):>10,}건  "
          f"(운행 {len(trips):,} − 차량 {n_vehicles} = 차량별 마지막 운행은 다음 배차가 없다)")

    neg = gaps["idle_min"] < 0
    print(f"  음수 구간 {int(neg.sum()):,}건 — 다음 배차가 직전 하차보다 앞선 기록"
          f"(중복 배차). 분포 계산에서 뺀다.")

    valid = gaps[~neg]
    same = valid[valid["is_same_day"]]
    cross = valid[~valid["is_same_day"]]

    def dist(g, label):
        q = g["idle_min"].quantile([0.5, 0.9]).to_numpy()
        print(f"  {label:<14} {len(g):>10,}건  중앙 {q[0]:7.1f}분  p90 {q[1]:8.1f}분  "
              f"평균 {g['idle_min'].mean():8.1f}분  최대 {g['idle_min'].max():9.1f}분")

    print("\n[구간 길이 분포]")
    dist(valid, "전체")
    dist(same, "당일 내")
    dist(cross, "자정 넘김")
    print("  자정을 넘긴 구간은 근무 종료~다음 근무 시작(비운행 시간)이 섞여 있다.")

    n_days = int(same["start"].dt.normalize().nunique())
    by_hour = hourly_concurrency(same, n_days)
    print(f"\n[시간대별 평균 동시 유휴 차량 수] 당일 내 구간 {len(same):,}건 · {n_days}일 기준")
    for _, r in by_hour.iterrows():
        bar = "█" * int(round(r["mean_idle_vehicles"] / 2))
        print(f"  {int(r['hour']):>2}시  {r['mean_idle_vehicles']:6.1f}대  {bar}")

    peak = by_hour.loc[by_hour["mean_idle_vehicles"].idxmax()]
    print(f"  최대 {int(peak['hour'])}시 {peak['mean_idle_vehicles']:.1f}대 / "
          f"차고지 정원 합 691대 대비 {peak['mean_idle_vehicles'] / 691:.1%}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame([{
        "n_trips": len(trips),
        "n_vehicles": n_vehicles,
        "n_gaps": len(gaps),
        "n_gaps_negative": int(neg.sum()),
        "n_gaps_same_day": len(same),
        "n_gaps_cross_day": len(cross),
        "idle_min_median": float(valid["idle_min"].median()),
        "idle_min_p90": float(valid["idle_min"].quantile(0.9)),
        "idle_min_median_same_day": float(same["idle_min"].median()),
        "idle_min_p90_same_day": float(same["idle_min"].quantile(0.9)),
        "n_days": n_days,
        "peak_hour": int(peak["hour"]),
        "peak_mean_idle_vehicles": float(peak["mean_idle_vehicles"]),
    }])
    summary.to_csv(OUT_DIR / "idle_gaps.csv", index=False, encoding="utf-8-sig")
    by_hour.to_csv(OUT_DIR / "idle_by_hour.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT_DIR / 'idle_gaps.csv'} / {OUT_DIR / 'idle_by_hour.csv'}")


if __name__ == "__main__":
    main()
