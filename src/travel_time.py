"""
travel_time.py — 이동시간 산출

방식(결정): 실측 주력 + 거리 보조. ML은 향후 유보.
  1순위  OD쌍 실측 평균 (10건 이상, 실제 수요의 88.4% 커버)
  2순위  실측 속도 테이블로 거리→시간 (희소 구간, 승차거리 활용)
  3순위  네트워크 거리(OSM) — 미관측 극소수 안전망
"""
import pandas as pd

# 신뢰 구간 최소 건수(이하는 거리 보조로 전환)
MIN_TRIPS = 10


def build_travel_table(calls: pd.DataFrame) -> pd.DataFrame:
    """실측 탑승내역에서 (출발동, 목적동, 시간대) → 평균 운행시간 테이블 구축.

    - 운행시간 = 하차 − 승차 (승차·하차 완료건만)
    - 이상치 제거(<=0, >180분)
    - 시간대 구분(아침/낮/저녁/심야)
    - 건수 함께 저장(신뢰도 판정용)
    반환: 조회용 테이블
    """
    raise NotImplementedError


def build_speed_table(calls: pd.DataFrame) -> pd.DataFrame:
    """거리대·시간대별 평균 속도 테이블(희소 OD쌍 보조용)."""
    raise NotImplementedError


def get_travel_minutes(origin, dest, hour) -> float:
    """이동시간 조회. 실측 우선, 없으면 거리 보조, 그것도 없으면 네트워크 거리."""
    raise NotImplementedError
