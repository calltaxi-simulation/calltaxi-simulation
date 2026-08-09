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


def test_map_customdata_carries_cand_id(cand):
    """후보 점 클릭이 3페이지로 가려면 customdata 에 cand_id 가 있어야 한다."""
    poly = D.resolve_polygons.__wrapped__()
    fig = D.build_map(poly, None, True, True, D.DEFAULT_METRIC)
    tr = [t for t in fig.data if getattr(t, "name", "") == "후보 주차장"]
    assert tr, "후보 레이어가 없다"
    cds = tr[0].customdata
    assert len(cds) == len(cand)
    assert all(c[0] == D.CAND_TAG and isinstance(c[7], int) for c in cds)
    assert {c[7] for c in cds} == set(cand["cand_id"].astype(int))


# ─────────────────────────────────────────────────────────────
# 3페이지 — 시뮬 결과
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def placement():
    g = D.load_placement.__wrapped__()
    if g.empty:
        pytest.skip("placement_grades.csv 가 없다")
    return g


def test_placement_drops_grade_columns(placement):
    """**등급 열은 화면 코드에 도달하면 안 된다.**

    안 쓰는 것으로는 부족하다 — 열이 남아 있으면 다음 사람이 집어 쓴다.
    로더에서 잘라 두면 3페이지가 등급에 손댈 방법 자체가 없다.
    """
    assert not [c for c in placement.columns if c.startswith(D.REF_PREFIX)]
    for banned in ("grade", "gap_prev", "threshold_prev"):
        assert banned not in placement.columns
    # 화면이 쓰는 열은 그대로 있어야 한다
    for keep in ("rate_pct", "interval", "n_overlap", "sample_ok",
                 "total_delta_min", "n_calls_scope", "n_dong_scope", "after"):
        assert keep in placement.columns


def test_scope_reproduces_recorded_dong_count(placement):
    """**다시 잡은 영향권의 동 수가 CSV 의 `n_dong_scope` 와 같아야 한다.**

    영향권은 저장돼 있지 않아 `evaluate.scope_members` 로 다시 잡는다. 반경이나
    좌표 소스가 어긋나면 지도와 숫자가 조용히 갈라진다 — 여기서 잡는다.
    """
    scope = D.placement_scope.__wrapped__()
    assert len(scope) == len(placement) == 244
    bad = [(int(r.cand_id), scope[int(r.cand_id)]["n_dong"], int(r.n_dong_scope))
           for r in placement.itertuples()
           if scope[int(r.cand_id)]["n_dong"] != int(r.n_dong_scope)]
    assert not bad, f"영향권 동 수가 어긋난다: {bad[:5]}"
    for r in placement.itertuples():
        s = scope[int(r.cand_id)]
        assert s["assigned"], f"cand {r.cand_id} 담당 동이 비었다"
        assert s["lat"] is not None and s["lon"] is not None


def test_unmappable_dongs_are_counted_not_hidden(placement):
    """경계가 없거나 합쳐진 동은 **세어서 알린다.**

    경계 파일(2023)과 콜 원본의 동 구분이 달라 폴리곤 수가 동 수보다 적을 수
    있다(244곳 중 66곳). 침묵하면 지도가 "18개 동"이라 적고 17개를 칠한다.
    """
    scope = D.placement_scope.__wrapped__()
    gap = 0
    for s in scope.values():
        assert len(s["polys"]) <= s["n_dong"], "폴리곤이 동보다 많을 수는 없다"
        assert s["n_unmapped"] == s["n_dong"] - len(s["polys"])
        gap += bool(s["n_unmapped"])
    assert gap > 0, "어긋남이 사라졌다면 이 경고 문구도 걷어내야 한다"
    assert gap == 66, f"어긋나는 후보 수가 바뀌었다: {gap} (전에는 66)"


def test_sim_map_paints_exactly_the_scope(placement):
    """영향권만 칠하고 담당 동은 테두리로 가른다."""
    scope = D.placement_scope.__wrapped__()
    for cid in (380, 411, 515):
        row = placement[placement["cand_id"] == cid].iloc[0]
        fig = D.build_sim_map(cid, row)
        names = [t.name for t in fig.data]
        assert names == ["영향권 밖", "3km 영향권", "담당 동", "후보"]

        painted = sum(1 for v in fig.data[1].z if v is not None)
        assert painted == len(scope[cid]["polys"])
        n_asg = sum(1 for v in fig.data[2].z if v is not None)
        assert n_asg == len(scope[cid]["assigned"])
        # 담당 동은 영향권의 부분집합이라 채움이 같아야 한다 — 테두리만 다르다
        assert fig.data[2].colorscale == fig.data[1].colorscale
        assert fig.data[2].marker.line.width > fig.data[1].marker.line.width


def test_sim_map_uirevision_is_per_candidate(placement):
    """후보를 바꾸면 카메라를 다시 잡아야 한다 — "constant" 면 화면 밖에 남는다."""
    rows = {c: placement[placement["cand_id"] == c].iloc[0] for c in (380, 411)}
    revs = {c: D.build_sim_map(c, r).layout.uirevision for c, r in rows.items()}
    assert revs[380] != revs[411]
    # 같은 후보를 다시 그리면 값이 같아야 줌·팬이 유지된다
    assert D.build_sim_map(380, rows[380]).layout.uirevision == revs[380]


def test_improve_color_is_monotone():
    """개선율이 클수록 짙어야 한다 — 후보 간 비교가 색의 유일한 용도다."""
    light = D.improve_color(-1.5, 1.5, 8.6)
    dark = D.improve_color(-8.6, 1.5, 8.6)
    assert light == D.IMPROVE_LIGHT and dark == D.IMPROVE_DARK
    lum = lambda h: sum(int(h[i:i + 2], 16) for i in (1, 3, 5))  # noqa: E731
    assert lum(light) > lum(D.improve_color(-5.0, 1.5, 8.6)) > lum(dark)


def test_candidate_options_sort_and_flag(placement):
    """정렬 두 축 · 표본 부족 표시 · **순위 번호 없음**."""
    by_rate = D.candidate_options("rate")
    by_total = D.candidate_options("total")
    assert len(by_rate) == len(by_total) == len(placement)

    # 정렬 축이 실제로 다르다 — 같으면 둘 중 하나가 무의미하다
    assert [c for c, _ in by_rate] != [c for c, _ in by_total]
    assert by_rate[0][0] == int(placement.sort_values("rate_pct").iloc[0]["cand_id"])
    assert by_total[0][0] == int(
        placement.sort_values("total_delta_min").iloc[0]["cand_id"])

    # 두 축의 값이 라벨에 함께 있어야 한다 — 정렬 키만 보이면 그 축이 정답으로 읽힌다
    label = dict(by_rate)[by_rate[0][0]]
    assert "%" in label and "분" in label

    weak = {int(r.cand_id) for r in placement.itertuples() if not r.sample_ok}
    for cid, lab in by_rate:
        assert ("※표본부족" in lab) == (cid in weak)

    # 순위 번호를 붙이지 않는다
    for _, lab in by_rate[:5]:
        assert not lab.lstrip().split(".")[0].isdigit()
        assert "위" not in lab
