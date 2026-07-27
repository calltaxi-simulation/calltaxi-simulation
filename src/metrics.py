"""
metrics.py — 성과지표 계산

시뮬 로그(콜별 대기시간 등)에서 관문 C 채점표 지표를 산출.
효율 / 형평 / 비용 세 축.
"""
import numpy as np
import pandas as pd

# 관문 C 캘리브레이션 타깃(현행 배치 재현 목표)
TARGETS = {
    "mean_wait": 39.3,
    "median_wait": 30.8,
    "p90_wait": 77.2,
    "long_wait_ratio": 0.179,   # 60분 초과
    "cancel_ratio": 0.142,
    "gini_dong": 0.095,
}


def efficiency(log: pd.DataFrame) -> dict:
    """효율: 평균 대기, 픽업거리, 공차율."""
    raise NotImplementedError


def equity(log: pd.DataFrame) -> dict:
    """형평: 동간 대기 격차(지니), 3km 커버, 장기대기(60분 초과) 비율.

    지니는 콜수 가중, 100건 미만 동 제외(D-04).
    """
    raise NotImplementedError


def cost(placement: pd.DataFrame) -> dict:
    """비용: 배치안 연간 사용료(3시나리오: 무상/감면50/전액) + 개선단위당 비용."""
    raise NotImplementedError


def gini(values, weights=None) -> float:
    """지니계수(콜수 가중 가능)."""
    raise NotImplementedError


def compare_to_target(result: dict) -> pd.DataFrame:
    """관문 C: 산출값 vs 타깃 대조표."""
    raise NotImplementedError
