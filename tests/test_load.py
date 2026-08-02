"""
test_load.py — 데이터 로딩 검증 (코드 점검)

동 이름 정규화와 좌표 매칭이 조용히 깨지면 이후 지표가 전부 어긋난다.
콜 원본을 읽는 테스트는 느려서 slow 마커를 붙였다: `pytest -m "not slow"` 로 제외.
"""
from pathlib import Path

import pytest

import load

# 서울 대략적 경계 — 좌표가 엉뚱한 곳으로 튀는지 잡는 용도
SEOUL_BBOX = (37.41, 37.71, 126.76, 127.19)   # lat_min, lat_max, lon_min, lon_max


def in_seoul(lat, lon):
    la0, la1, lo0, lo1 = SEOUL_BBOX
    return (lat.between(la0, la1) & lon.between(lo0, lo1)).all()


# ─────────────────────────────────────────────────────────────
# 데이터 경로 해석
# ─────────────────────────────────────────────────────────────

def test_data_dir_defaults_to_sibling_data(monkeypatch):
    """환경변수가 없으면 저장소 상위의 data/ 를 본다."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert load.resolve_data_dir() == load.DEFAULT_DATA_DIR
    assert load.DEFAULT_DATA_DIR.name == "data"


def test_data_dir_env_override(monkeypatch, tmp_path):
    """DATA_DIR 을 주면 그 폴더를 본다 — clone 구조가 달라도 돌아가게."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert load.resolve_data_dir() == tmp_path.resolve()


def test_data_dir_env_blank_falls_back(monkeypatch):
    """빈 값·공백만 있으면 설정 안 한 것으로 본다."""
    monkeypatch.setenv("DATA_DIR", "   ")
    assert load.resolve_data_dir() == load.DEFAULT_DATA_DIR


def test_data_dir_env_expands_user(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "~/calltaxi-data")
    assert load.resolve_data_dir() == (Path.home() / "calltaxi-data").resolve()


# ─────────────────────────────────────────────────────────────
# 동 이름 정규화
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("고덕제1동", "고덕1동"),
    ("성수2가제3동", "성수2가3동"),      # '제'가 두 번 나오는 중첩 케이스
    ("공릉1.3동", "공릉13동"),           # 마침표 제거
    ("사직동", "사직동"),                # 숫자 없으면 그대로
    ("종로1.2.3.4가동", "종로1234가동"),
])
def test_canon_dong(raw, expected):
    assert load.canon_dong(raw) == expected


def test_canon_dong_is_idempotent():
    """이미 표준형인 이름을 다시 넣어도 바뀌지 않아야 한다."""
    for raw in ["고덕제1동", "성수2가제3동", "제기제1동", "신당제6동"]:
        once = load.canon_dong(raw)
        assert load.canon_dong(once) == once


def test_canon_dong_keeps_leading_je():
    """'제기동'의 '제'는 숫자 앞이 아니므로 지워지면 안 된다."""
    assert load.canon_dong("제기동") == "제기동"
    assert load.canon_dong("제기제1동") == "제기1동"


# ─────────────────────────────────────────────────────────────
# 조회표·보조 테이블
# ─────────────────────────────────────────────────────────────

def test_dong_lookup_keys_unique(data_dir):
    lk = load.build_dong_lookup()
    assert not lk.duplicated(subset=["gu", "key", "key_kind"]).any()
    assert set(lk["key_kind"]) == {"canon", "base"}
    assert set(lk["match_type"]) == {"exact", "approx", "manual"}


def test_dong_lookup_covers_all_seoul_gu(data_dir):
    lk = load.build_dong_lookup()
    assert set(lk[lk["match_type"] == "exact"]["gu"]) == set(load.SEOUL_GU)


def test_dong_lookup_manual_overrides_resolve(data_dir):
    """개편으로 사라진 동이 후신 동 좌표를 받아야 한다."""
    lk = load.build_dong_lookup()
    canon = lk[lk["key_kind"] == "canon"].set_index(["gu", "key"])
    for (gu, src), target in load.MANUAL_DONG.items():
        assert (gu, load.canon_dong(src)) in canon.index, f"{gu} {src} 미해결"
        got = canon.loc[(gu, load.canon_dong(src))]
        want = canon.loc[(gu, load.canon_dong(target))]
        assert got["lat"] == want["lat"] and got["lon"] == want["lon"]


def test_load_depots(data_dir):
    dep = load.load_depots()
    assert len(dep) == 44
    assert dep["capacity"].sum() == 691
    assert dep[["lat", "lon"]].notna().all().all()
    assert in_seoul(dep["lat"], dep["lon"])
    assert dep["gu"].isin(load.SEOUL_GU).all()
    assert not dep["depot_id"].duplicated().any()


def test_load_dong_centroids(data_dir):
    cen = load.load_dong()
    assert len(cen) == 426
    assert in_seoul(cen["lat"], cen["lon"])
    assert not cen.duplicated(subset=["gu", "dong_canon"]).any()


def test_load_candidates(data_dir):
    c = load.load_candidates()
    assert len(c) > 0
    assert in_seoul(c["lat"], c["lon"])
    assert c["gu"].isin(load.SEOUL_GU).all()
    assert (c["capacity"] >= 0).all()
    # 좌표 없는 건은 전부 구영이라는 사실이 유지되는지 (바뀌면 후보지 풀 해석이 달라진다)
    assert c.attrs["n_dropped_no_coord"] == c.attrs["n_dropped_district_run"]


def test_load_undersupplied(data_dir):
    u = load.load_undersupplied()
    assert (u["vehicles_3km"] <= 10).all()
    assert u["long_wait_ratio"].between(0, 1).all()
    assert in_seoul(u["lat"], u["lon"])
    # 임계를 올리면 대상 동이 늘어야 한다
    assert len(load.load_undersupplied(max_vehicles_3km=30)) > len(u)


# ─────────────────────────────────────────────────────────────
# 콜 원본 (느림)
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def calls(data_dir):
    return load.load_calls()


@pytest.mark.slow
def test_calls_filters_applied(calls):
    assert calls["origin_gu"].isin(load.SEOUL_GU).all()
    gap = (calls["scheduled_at"] - calls["received_at"]).abs().dt.total_seconds() / 60
    assert (gap <= load.IMMEDIATE_TOLERANCE_MIN).all()
    assert calls["origin_lat"].notna().all(), "출발 좌표는 배차에 필수"
    assert not calls["call_id"].duplicated().any()


@pytest.mark.slow
def test_calls_wait_time_sane(calls):
    w = calls["wait_min"].dropna()
    assert (w >= 0).all(), "대기시간은 음수가 될 수 없다"
    assert (w <= load.MAX_WAIT_MIN).all()
    # 취소건은 승차 기록이 없으므로 대기시간도 없어야 한다
    assert calls.loc[calls["is_canceled"], "wait_min"].isna().all()


@pytest.mark.slow
def test_calls_reproduce_observed_targets(calls):
    """검증 기준이 실측에서 재현되는지 — 필터 정의가 맞는지 확인."""
    w = calls["wait_min"].dropna()
    assert w.mean() == pytest.approx(39.3, abs=0.3)
    assert w.median() == pytest.approx(30.8, abs=0.3)
    assert w.quantile(0.90) == pytest.approx(77.2, abs=0.5)
    assert (w > 60).mean() == pytest.approx(0.179, abs=0.005)


@pytest.mark.slow
def test_calls_period_matches_hour(calls):
    """시간대 파생이 시각과 어긋나지 않아야 한다(travel_time 이 이 키를 쓴다)."""
    assert set(calls["period"].unique()) <= set(load.PERIOD_LABELS)
    assert (calls.loc[calls["hour"] == 3, "period"] == "심야").all()
    assert (calls.loc[calls["hour"] == 8, "period"] == "아침").all()
    assert (calls.loc[calls["hour"] == 14, "period"] == "낮").all()
    assert (calls.loc[calls["hour"] == 20, "period"] == "저녁").all()
    assert (calls.loc[calls["hour"] == 23, "period"] == "심야").all()
