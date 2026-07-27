"""
test_metrics.py — 지니·동별 집계 검증 (관문 B)

지니는 값이 조용히 틀려도 그럴듯해 보여서 눈으로 못 잡는다.
해석적으로 답이 정해진 케이스로 고정해 둔다.
"""
import numpy as np
import pandas as pd
import pytest

import load
import metrics


# ─────────────────────────────────────────────────────────────
# 지니계수
# ─────────────────────────────────────────────────────────────

def test_gini_perfectly_equal():
    assert metrics.gini([5, 5, 5, 5]) == pytest.approx(0.0)


def test_gini_perfectly_unequal():
    """한 명이 전부 가지면 (n−1)/n 로 수렴한다."""
    assert metrics.gini([0, 0, 0, 100]) == pytest.approx(0.75)
    assert metrics.gini([0] * 99 + [1]) == pytest.approx(0.99)


def test_gini_known_value():
    """[1..5] 의 지니는 해석적으로 4/15."""
    assert metrics.gini([1, 2, 3, 4, 5]) == pytest.approx(4 / 15)


def test_gini_weights_equal_repetition():
    """가중치는 값 반복과 같은 결과여야 한다."""
    assert metrics.gini([1, 2], [3, 1]) == pytest.approx(metrics.gini([1, 1, 1, 2]))
    assert metrics.gini([10, 20, 30], [2, 2, 2]) == pytest.approx(
        metrics.gini([10, 20, 30]))


def test_gini_scale_invariant():
    """전부 상수배해도 지니는 그대로."""
    v = [3.0, 7.5, 12.0, 40.0]
    assert metrics.gini(v) == pytest.approx(metrics.gini(np.array(v) * 7.3))


def test_gini_ignores_nan_and_zero_weight():
    assert metrics.gini([1, 2, 3, np.nan]) == pytest.approx(metrics.gini([1, 2, 3]))
    assert metrics.gini([1, 2, 3, 999], [1, 1, 1, 0]) == pytest.approx(
        metrics.gini([1, 2, 3]))


def test_gini_rejects_negative():
    with pytest.raises(ValueError):
        metrics.gini([-1, 2, 3])


def test_gini_length_mismatch():
    with pytest.raises(ValueError):
        metrics.gini([1, 2, 3], [1, 1])


def test_gini_empty_is_nan():
    assert np.isnan(metrics.gini([]))
    assert np.isnan(metrics.gini([np.nan, np.nan]))


# ─────────────────────────────────────────────────────────────
# 동별 집계 · D-04
# ─────────────────────────────────────────────────────────────

def _toy_log(counts):
    """{(구, 동): 건수} → 최소 스키마 로그."""
    rows = []
    for (gu, dong), n in counts.items():
        rows += [{"origin_gu": gu, "origin_dong": dong, "wait_min": 30.0}] * n
    return pd.DataFrame(rows)


def test_dong_table_applies_min_calls():
    log = _toy_log({("A구", "큰동"): 150, ("A구", "작은동"): 99})
    tbl = metrics.dong_wait_table(log)
    assert list(tbl["dong"]) == ["큰동"]
    assert tbl.attrs["n_dong_excluded"] == 1
    assert list(tbl.attrs["excluded"]["dong"]) == ["작은동"]


def test_dong_table_boundary_is_inclusive():
    """정확히 100건인 동은 남아야 한다(D-04 는 '100건 미만' 제외)."""
    log = _toy_log({("A구", "경계동"): 100})
    assert len(metrics.dong_wait_table(log)) == 1


def test_dong_table_excludes_cancels_from_count():
    """취소 건(wait=NaN)은 건수에 잡히면 안 된다 — 원본 집계와 정의를 맞춘다."""
    log = pd.DataFrame({
        "origin_gu": ["A구"] * 200,
        "origin_dong": ["동"] * 200,
        "wait_min": [30.0] * 120 + [np.nan] * 80,
    })
    tbl = metrics.dong_wait_table(log, min_calls=100)
    assert tbl.loc[0, "n_calls"] == 120
    assert tbl.loc[0, "mean_wait"] == pytest.approx(30.0)


# ─────────────────────────────────────────────────────────────
# 실측 재현 (느림)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def calls(data_dir):
    return load.load_calls()


@pytest.mark.slow
def test_dong_count_matches_original_analysis(calls):
    """D-04 적용 시 원본 분석과 같은 430개 동이 남아야 한다."""
    tbl = metrics.dong_wait_table(calls)
    assert len(tbl) == 430
    excluded = set(tbl.attrs["excluded"]["dong"])
    assert excluded == {"창신제3동", "반포본동"}


@pytest.mark.slow
def test_dong_means_match_original_analysis(calls, data_dir):
    """동별 평균대기가 원본 산출물과 일치해야 한다(원본은 소수 1자리 반올림)."""
    orig = pd.read_csv(load.DATA_DIR / "서울즉시콜_동별_대기격차.csv", encoding="utf-8-sig")
    orig = orig.rename(columns={"출발구": "gu", "출발동": "dong",
                                "평균": "orig_mean", "건수": "orig_n"})
    m = metrics.dong_wait_table(calls).merge(orig, on=["gu", "dong"], how="inner")
    assert len(m) == 430
    assert (m["n_calls"] == m["orig_n"]).all(), "동별 건수가 원본과 어긋난다"
    assert (m["mean_wait"] - m["orig_mean"]).abs().max() < 0.06


@pytest.mark.slow
def test_gini_reproduces_target(calls):
    """관문 C 타깃 0.095 는 균등가중 기준으로 재현된다(README † 각주)."""
    assert metrics.dong_gini(calls) == pytest.approx(
        metrics.TARGETS["gini_dong"], abs=0.002)
    # 콜수 가중은 다른 값이 나온다 — 정의가 갈린다는 사실 자체를 고정해 둔다
    assert metrics.dong_gini(calls, weighted=True) == pytest.approx(0.101, abs=0.002)


@pytest.mark.slow
def test_cancel_ratio_matches_corrected_target(calls):
    """정정된 취소율 타깃 13.3% 가 시뮬 입력 모수에서 재현되는지."""
    assert calls["is_canceled"].mean() == pytest.approx(
        metrics.TARGETS["cancel_ratio"], abs=0.002)
