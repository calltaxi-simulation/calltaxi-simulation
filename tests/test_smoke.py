"""
test_smoke.py — 코드 점검 기본 테스트

코드가 논리대로 도는지 확인하는 극단 케이스.
실제 테스트는 코딩 진행하며 채운다.
"""
import pytest


@pytest.mark.skip(reason="구현 후 활성화")
def test_travel_time_non_negative():
    """이동시간은 음수가 될 수 없다."""
    pass


@pytest.mark.skip(reason="구현 후 활성화")
def test_zero_calls():
    """콜 0건이어도 시뮬이 터지지 않는다."""
    pass


@pytest.mark.skip(reason="구현 후 활성화")
def test_single_vehicle():
    """차량 1대여도 정상 동작한다."""
    pass
