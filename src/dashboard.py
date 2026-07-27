"""
dashboard.py — Streamlit 대시보드

실무자가 후보지를 토글로 거르고, 배치안을 만들어 시뮬 결과를 본다.
데모데이: 사전계산 3안(효율/형평/균형) 비교 + 라이브 데모 1~2개.

실행: streamlit run src/dashboard.py
"""
import streamlit as st


def sidebar_filters():
    """후보지 필터 토글: 소유(직영/자치구/민간), 유형(노상/노외/부설), 유무료, 이용효율."""
    raise NotImplementedError


def map_view():
    """지도: 차고지·후보지·과소공급 동 표시."""
    raise NotImplementedError


def result_panel():
    """배치안 선택 → 성과지표 표시(현행 대비 개선). 사전계산 결과 우선."""
    raise NotImplementedError


def main():
    st.set_page_config(page_title="장애인콜택시 대기거점 대시보드", layout="wide")
    st.title("장애인콜택시 대기거점 입지 평가")
    # sidebar_filters(); map_view(); result_panel()
    raise NotImplementedError


if __name__ == "__main__":
    main()
