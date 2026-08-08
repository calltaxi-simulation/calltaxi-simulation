# -*- coding: utf-8 -*-
"""후보 평가 점검 — 이어 돌리기와 등급 규칙.

이 둘은 **조용히 틀리면 잡히지 않는다.** 이어 돌리기가 깨지면 후보 몇 개가
말없이 빠진 채 등급이 매겨지고, 등급 규칙이 어긋나면 구분되지 않는 후보가
다른 등급으로 갈려 실무자가 근거 없는 선택을 하게 된다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import evaluate as E   # noqa: E402


def _rows(spec, stage=2):
    """(cand_id, [시드별 개선율]) → 결과 CSV 형식 행들."""
    out = []
    for cid, rates in spec:
        for seed, r in enumerate(rates, start=42):
            out.append({"stage": stage, "cand_id": cid, "cand_name": f"c{cid}",
                        "gu": "종로구", "dongs": "종로구 사직동", "capacity": 10,
                        "seed": seed, "n_dong_scope": 20, "n_calls_scope": 4000,
                        "before": 14.0, "after": 14.0 * (1 + r / 100),
                        "delta": 14.0 * r / 100, "rate_pct": r, "elapsed_s": 50.0})
    return pd.DataFrame(out, columns=E._COLUMNS)


# ─────────────────────────────────────────────────────────────
# 이어 돌리기
# ─────────────────────────────────────────────────────────────

def test_append_and_resume(tmp_path):
    """덧붙이기 → 다시 읽기. 헤더가 한 번만 붙어야 한다."""
    p = tmp_path / "eval.csv"
    assert E.load_results(p).empty

    for i in (1, 2, 3):
        E._append({c: (i if c == "cand_id" else 0) for c in E._COLUMNS}, p)
    got = E.load_results(p)
    assert len(got) == 3
    assert list(got["cand_id"]) == [1, 2, 3]
    assert list(got.columns) == E._COLUMNS


def test_resume_skips_done_pairs(tmp_path, monkeypatch):
    """이미 있는 (cand_id, seed) 는 다시 돌리지 않는다."""
    p = tmp_path / "eval.csv"
    _rows([(1, [-3.0]), (2, [-1.0])], stage=1).to_csv(
        p, index=False, encoding="utf-8-sig")

    cands = pd.DataFrame({"cand_id": [1, 2, 3], "cand_name": ["a", "b", "c"],
                          "lat": [37.5] * 3, "lon": [127.0] * 3,
                          "capacity": [10] * 3, "gu": ["종로구"] * 3,
                          "dongs": ["x"] * 3, "n_dong_assigned": [1] * 3})

    ran = []
    monkeypatch.setattr(E.S, "run_placement",
                        lambda pl, *a, **k: ran.append(pl) or pd.DataFrame())
    monkeypatch.setattr(E, "scope_members", lambda *a, **k: {("종로구", "사직동")})
    monkeypatch.setattr(E, "scope_pickup", lambda log, m: (14.0, 4000))

    E.run_stage(1, cands, (42,), calls=None, depots=None, mx=None,
                reservation=None, dong_xy=None, path=p)
    # 후보 3만 남았어야 한다 — before(None) 1회 + 후보 1회
    assert ran == [None, 3], f"이미 끝난 후보를 다시 돌렸다: {ran}"
    assert len(E.load_results(p)) == 3


def test_shortlist_is_by_improvement_rate():
    """2단계 대상은 개선율(음수가 클수록 좋음) 상위 N."""
    res = _rows([(1, [-5.0]), (2, [-1.0]), (3, [-3.0]), (4, [+0.5])], stage=1)
    assert E.stage2_shortlist(res, 2) == [1, 3]


# ─────────────────────────────────────────────────────────────
# 등급 — σ 기반
# ─────────────────────────────────────────────────────────────

def test_grades_merge_when_gap_below_threshold():
    """차이가 2σ 에 못 미치면 같은 등급으로 묶인다."""
    # σ ≈ 0.1 → 문턱 ≈ 2·√(0.01+0.01) ≈ 0.283. 간격 0.1 은 그보다 좁다.
    res = _rows([(1, [-5.0, -5.1, -4.9]), (2, [-4.9, -5.0, -4.8])])
    g = E.assign_grades(res)
    assert g["grade"].nunique() == 1
    assert set(g["grade"]) == {"단일"}


def test_grades_split_when_gap_exceeds_threshold():
    """차이가 2σ 를 넘으면 등급이 갈린다."""
    res = _rows([(1, [-5.0, -5.1, -4.9]), (2, [-1.0, -1.1, -0.9])])
    g = E.assign_grades(res)
    assert g["grade"].nunique() == 2
    assert list(g["grade"]) == ["상", "하"]
    assert g.loc[0, "cand_id"] == 1, "개선이 큰 후보가 위에 와야 한다"
    assert g.loc[1, "gap_prev"] == pytest.approx(4.0, abs=0.01)


def test_grade_threshold_uses_combined_sigma():
    """문턱은 k·sqrt(σᵢ²+σⱼ²) — 두 후보의 σ 를 합친 값이다."""
    res = _rows([(1, [-5.0, -5.6, -4.4]), (2, [-3.6, -3.7, -3.5])])
    g = E.assign_grades(res)
    sd = g["rate_sd"].to_numpy()
    assert g.loc[1, "threshold_prev"] == pytest.approx(
        E.GRADE_SIGMA_K * np.sqrt(sd[0] ** 2 + sd[1] ** 2), rel=1e-9)


def test_grades_use_stage1_replicate_too():
    """2단계 대상은 1단계에서 돈 시드도 반복으로 센다 — σ 표본을 잃지 않는다."""
    s1 = _rows([(1, [-5.0]), (2, [-1.0])], stage=1)          # seed 42
    s2 = _rows([(1, [-5.1, -4.9]), (2, [-1.1, -0.9])], stage=2)
    s2["seed"] = s2["seed"] + 1                               # seed 43, 44
    g = E.assign_grades(pd.concat([s1, s2], ignore_index=True))
    assert set(g["n_seed"]) == {3}, "1단계 반복이 빠졌다"


def test_grades_need_two_seeds():
    """σ 를 낼 수 없으면 등급을 매기지 않고 멈춘다 — 조용히 넘기면 안 된다."""
    with pytest.raises(ValueError):
        E.assign_grades(_rows([(1, [-5.0]), (2, [-1.0])]))


def test_grades_are_monotone_in_rate():
    """등급은 개선율 순서를 뒤집지 않는다."""
    res = _rows([(i, [-r, -r - 0.05, -r + 0.05])
                 for i, r in enumerate([6.0, 4.0, 2.0, 1.9, 0.2], start=1)])
    g = E.assign_grades(res)
    assert g["rate_pct"].is_monotonic_increasing
    codes = pd.Categorical(g["grade"], categories=list(dict.fromkeys(g["grade"])),
                           ordered=True).codes
    assert (np.diff(codes) >= 0).all(), "등급이 개선율 순서와 어긋난다"


def test_grade_report_carries_the_warnings():
    """보고 문구에 '등급 안에서는 우열을 가릴 수 없다'와 과소평가가 있어야 한다."""
    g = E.assign_grades(_rows([(1, [-5.0, -5.1, -4.9]), (2, [-1.0, -1.1, -0.9])]))
    txt = E.grade_report(g)
    assert "우열을 가릴 수 없다" in txt
    assert "과소평가" in txt


# ─────────────────────────────────────────────────────────────
# 범위
# ─────────────────────────────────────────────────────────────

def test_scope_members_uses_radius():
    dong = pd.DataFrame({"gu": ["A", "A", "B"], "dong_canon": ["가", "나", "다"],
                         "lat": [37.5, 37.52, 37.9], "lon": [127.0, 127.0, 127.0]})
    got = E.scope_members(37.5, 127.0, dong, radius_km=3.0)
    assert got == {("A", "가"), ("A", "나")}          # 다동은 44km 밖
    assert E.scope_members(37.5, 127.0, dong, radius_km=0.1) == {("A", "가")}


def test_scope_pickup_is_call_weighted():
    """콜 가중 — 동 균등이 아니다. 큰 동이 더 무겁다."""
    log = pd.DataFrame({
        "origin_gu": ["A"] * 10 + ["A"] * 2 + ["B"] * 5,
        "origin_dong_canon": ["가"] * 10 + ["나"] * 2 + ["다"] * 5,
        "assigned_at": pd.Timestamp("2025-08-20 09:00"),
        "boarded_at": ([pd.Timestamp("2025-08-20 09:10")] * 10
                       + [pd.Timestamp("2025-08-20 09:40")] * 2
                       + [pd.Timestamp("2025-08-20 09:20")] * 5),
    })
    mean, n = E.scope_pickup(log, {("A", "가"), ("A", "나")})
    assert n == 12
    assert mean == pytest.approx((10 * 10 + 2 * 40) / 12)
