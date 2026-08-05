"""
simulator.py — 시뮬 엔진 (SimPy)

하루를 재생: 콜 발생 → 배차(점수제) → 이동 → 탑승 → 하차 → 대기.
배치안(거점 위치·수용대수)을 입력받아 성과지표를 산출하는 평가 엔진.

가정 — 정의는 ASSA(가정·단순화 로그, 저장소 밖 팀 문서)에 있다.
아래는 이 모듈이 참조하는 항목의 발췌다. docs/external_sources.md 참조.

  A-01  유휴 차량은 차고지로 복귀하지 않고 하차 지점 인근에서 제자리 대기하며,
        배차된 콜이 타 지역이면 공차로 이동한다
  A-12  거점은 시차 운행으로 이용된다 — 면수 ≠ 동시 주차 대수. 조별 출차 시각이
        분산되며(2025년 실측: 07시 25.7% / 08시 20.8% / 10시 11.8% / 12시 19.9% /
        13시 5.0%, 근무 6~8시간), 시뮬 초기화의 조별 출차 배정에 사용된다
  S-02  예약콜은 재생하지 않고 가용 차량에서 차감만 한다
"""
import numpy as np
import simpy

# 난수 시드(재현성 — 변경 시 기록)
SEED = 42

# 자동배차 탐색 반경(km) — 서울시설공단 장애인콜택시 이용 안내 확인분(미확정, 검증 필요)
# https://www.sisul.or.kr/open_content/calltaxi/introduce/receipt.jsp
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
