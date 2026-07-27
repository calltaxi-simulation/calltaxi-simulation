"""
simulator.py — 시뮬 엔진 (SimPy)

하루를 재생: 콜 발생 → 배차(점수제) → 이동 → 탑승 → 하차 → 대기.
배치안(거점 위치·수용대수)을 입력받아 성과지표를 산출하는 평가 엔진.

가정(ASSA 참조):
  A-01  유휴 차량 = 제자리 대기, 배차 콜이 타지역이면 공차 이동
  A-12  이동시간 = 실측 테이블
  S-02  예약콜 = 재생 안 하고 가용차량에서 차감
"""
import numpy as np
import simpy

# 난수 시드(재현성 — 변경 시 기록)
SEED = 42

# 자동배차 탐색 반경(km) — sisul.de 확인분(미확정, 검증 필요)
DISPATCH_RADIUS_DAY = 7
DISPATCH_RADIUS_NIGHT = 12


class CallTaxiSim:
    """시뮬레이션 본체."""

    def __init__(self, calls, depots, travel_time, seed=SEED):
        self.env = simpy.Environment()
        self.rng = np.random.default_rng(seed)
        # 콜·차량·거점·이동시간 세팅
        raise NotImplementedError

    def dispatch(self, call):
        """배차 로직: 점수제(거리 + 대기 + 접수). 가장 적합한 차량 선정."""
        raise NotImplementedError

    def vehicle_process(self, vehicle):
        """차량 1대의 생애: 대기 → 배차 → 픽업 이동 → 탑승 → 운행 → 하차 → 대기."""
        raise NotImplementedError

    def run(self):
        """시뮬 실행 후 콜별 대기시간 등 로그 반환."""
        raise NotImplementedError


def run_placement(placement, calls, depots, travel_time, seed=SEED):
    """배치안 하나를 평가. 반환: metrics 계산용 로그."""
    raise NotImplementedError


if __name__ == "__main__":
    print("simulator 뼈대 — 코딩 단계에서 구현")
