"""
simulator.py — 시뮬 엔진 (SimPy)

하루를 재생: 콜 발생 → 배차(점수제) → 이동 → 탑승 → 하차 → 대기.
배치안(거점 위치·수용대수)을 입력받아 성과지표를 산출하는 평가 엔진.

가정 — 정의는 ASSA(가정·단순화 로그) docs/assa_log.md 에 있다.
아래는 이 모듈이 참조하는 항목의 발췌다.

  A-01  유휴 차량은 차고지로 복귀하지 않고 하차 지점 인근에서 제자리 대기하며,
        배차된 콜이 타 지역이면 공차로 이동한다. 단 유휴 ≠ 가용 — 하차~다음 승차
        구간에는 식사·교대·충전·근무외가 섞여 있어 전부 배차 가능한 상태는 아니다
  A-09  거점은 시차 운행으로 이용된다 — 면수 ≠ 동시 주차 대수. 조별 출차 시각이
        분산되며(특장차 실측: 07시 28.4% / 08시 24.3% / 12시 23.8% / 13시 6.0% /
        09시 3.1%, 근무 중앙 6.72시간), 시뮬 초기화의 조별 출차 배정에 사용된다
  A-11  예약콜은 재생하지 않고, 시간대별 실측 수행 건수를 기반으로 가용 차량에서
        차감만 한다(외생 처리)
  A-16  모집단은 특장차 한정이다. 임차택시는 차고지 기반 교대 운영이 아니어서
        거점 배치의 영향을 받지 않는다

**차량 대수는 723 이 아니다.** 723 은 연간 누적 고유 차량번호이고, 하루에 실제로
나온 대수는 접수일 기준 중앙 561대(평일 574 / 주말 374, 범위 246~626)다.
723 을 넣으면 공급이 30% 과대해진다 — metrics.daily_active_vehicles() 참조.

**정산 미기록 616건(calibration.md 흡수 사실 ⑵)의 점유 시작은 `배차일시`다.** 승차 시각이 없지만
배차→하차 중앙 56.6분 동안 차량이 묶여 있었다. 재생에서 이 건을 취소로 흘리면
그만큼 공급이 과대해진다.
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
