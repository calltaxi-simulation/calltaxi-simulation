# -*- coding: utf-8 -*-
"""대시보드 로더 점검 — 사이드바가 읽는 attrs 가 살아 있는가.

**attrs 는 조용히 사라진다.** pandas `merge` 는 attrs 를 물려주지 않아서,
로더가 후보에 배정 동을 붙이는 순간 `n_pool`·`n_excluded_indoor_mixed` 가
날아갔다(KeyError: 'n_pool' — 사이드바 "후보 풀 {n}곳" 문구). 타입 검사에도
테스트에도 안 걸리고 화면을 켤 때만 터진다. 그래서 여기서 잡는다.
"""
import pytest

pytest.importorskip("streamlit", reason="대시보드 의존성 없음")

import dashboard as D   # noqa: E402
import load             # noqa: E402


# 사이드바가 f-string 에서 **직접** 꺼내 쓰는 키들. `.get()` 이 아니라 `[...]`
# 라서 없으면 곧바로 KeyError 다(dashboard.py 「레이어」 절).
SIDEBAR_ATTRS = ("n_pool", "n_excluded_indoor_mixed")


@pytest.fixture(scope="module")
def cand(data_dir):
    # st.cache_data 를 우회한다 — 캐시가 있으면 로더 본문을 안 타서
    # attrs 가 사라져도 통과해 버린다.
    return D.load_candidates.__wrapped__()


def test_loader_keeps_the_attrs_the_sidebar_reads(cand):
    """merge 뒤에도 원본 attrs 가 남아야 한다 — 이게 KeyError 의 원인이었다."""
    for key in SIDEBAR_ATTRS:
        assert key in cand.attrs, f"사이드바가 읽는 attrs 가 사라졌다: {key}"
    assert cand.attrs["n_pool"] == 639
    assert cand.attrs["n_excluded_indoor_mixed"] == 291
    # 로더 자신이 붙이는 값도 함께 있어야 한다(update 가 덮어쓰면 안 된다).
    assert cand.attrs["n_outdoor"] == 348


def test_loader_attrs_match_the_source(cand):
    """대시보드가 말하는 숫자와 load.py 가 말하는 숫자가 같아야 한다."""
    src = load.load_candidates()
    for key in SIDEBAR_ATTRS:
        assert cand.attrs[key] == src.attrs[key]


def test_assigned_subset_is_smaller_than_outdoor(cand):
    """배정된 곳만 남는다 — 지도가 시뮬 입력과 같은 것을 보여줘야 한다."""
    if not cand.attrs.get("assigned_only"):
        pytest.skip("dong_candidates.csv 가 없어 348곳 전체로 물러난 상태")
    assert len(cand) == 244
    assert len(cand) < cand.attrs["n_outdoor"] == 348
    assert cand["assigned_dongs"].str.len().gt(0).all()
    assert not cand["cand_id"].duplicated().any()


def test_sidebar_note_renders_without_keyerror(cand):
    """실제 문구를 그대로 조립해 본다 — 키가 빠지면 여기서 터진다."""
    note = (f"후보 풀 {cand.attrs['n_pool']}곳 중 옥외만 쓰는 이유는, "
            f"옥내·혼합 {cand.attrs['n_excluded_indoor_mixed']}곳이 …")
    assert "639" in note and "291" in note
