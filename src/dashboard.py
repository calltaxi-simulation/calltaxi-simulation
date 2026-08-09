# -*- coding: utf-8 -*-
"""
dashboard.py — 동별 대기 실태 진단 대시보드 (Streamlit)

실무자가 대기 실태를 동 단위로 보고 어디가 급선무인지 판단하는 도구다.
답(설치기준·추천)은 내지 않는다 — 근거만 보여준다.

화면 결정의 근거는 docs/dashboard_spec.md 에 있다. 이 파일에는 규칙을 적고
"왜 그런가"는 명세를 가리킨다.

실행: streamlit run src/dashboard.py

전 화면 2025년 연간 기준. 3페이지(시뮬)만 나중에 대표 기간 기준이 된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate as E  # noqa: E402
import load  # noqa: E402
import metrics as M  # noqa: E402
import simulator as S  # noqa: E402

OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"

APP_TITLE = "서울 장애인콜택시 대기 진단"
MAP_TITLE = "동별 대기 실태"
BASIS = "2025년 연간"

# ─────────────────────────────────────────────────────────────
# 상수 — 화면 규칙
# ─────────────────────────────────────────────────────────────

# 45분 미만을 한 색으로 접는 경계. 기관 기준이 아니라 시선을 좁히는 장치다.
# 근거 있는 임계가 생기면 여기만 바꾼다(명세 4절).
RED_THRESHOLD_MIN = 45.0

# 경계 단순화(도). 0.0002 ≈ 22m — 0.90MB → 0.40MB, 빈/불량 폴리곤 0 (명세 7절)
SIMPLIFY_TOL = 0.0002

# 타일 없는 배경. 외부 타일에 의존하면 오프라인에서 지도가 빈다(명세 7절).
MAP_STYLE = "white-bg"
MAP_CENTER = {"lat": 37.5665, "lon": 126.9780}
MAP_ZOOM = 10.2

# 색 — 데이터 색만 의미를 갖는다. 채도를 낮춰 형광이 되지 않게(명세 8절)
GREEN = "#5B8C6E"          # 접힌 구간 단일색
RED_LIGHT = "#E2A79E"      # 기준선
RED_DARK = "#8C2F26"       # 최댓값
GREY_LIGHT = "#DAD8D3"     # 회색조 하단
GREY_DARK = "#8B8880"      # 회색조 상단
NO_MATCH = "#EDEBE7"       # 지표 매칭 없음
INK = "#22201D"
BG = "#F4F3F0"

# 점 레이어 두 종 — 색으로 구분한다. 현행 차고지는 짙은 남색(운영 중),
# 후보는 주황(아직 아님). 크기도 9 vs 7 로 갈라 겹쳐도 읽히게 한다.
DEPOT_COLOR = "#2F4858"
CAND_COLOR = "#C8722B"
# 후보 점 클릭을 코로플레스 클릭과 구분하는 태그(customdata 첫 칸)
CAND_TAG = "cand"
PANEL_BG = "#FBFAF8"

PERIOD_ORDER = ["심야", "아침", "낮", "저녁"]

# ── 3페이지(시뮬) 전용 ────────────────────────────────────────
# 개선 농도 램프. 개선율이 클수록 짙다. **한 후보의 지도 안에서는 평평하다** —
# 후보별 before/after 는 3km 범위 전체의 집계 하나뿐이고 동별로는 없다.
# 농도는 후보끼리 비교하라고 두는 것이다(build_sim_map 주석 참조).
IMPROVE_LIGHT = "#D5E3D8"
IMPROVE_DARK = "#2E5E43"
OUT_SCOPE = "#E3E1DC"        # 영향권 밖 — 물러나되 사라지지는 않게
ASSIGNED_LINE = "#1F4230"    # 담당 동 테두리

# 시뮬 화면의 기준 기간. 진단 화면(연간)과 다르다 — 섞어 읽으면 안 되므로
# **시뮬 상수에서 끌어온다.** 화면에 직접 적으면 대표 기간이 바뀔 때 어긋난다.
SIM_START = pd.Timestamp(S.REPRESENTATIVE_START)
SIM_DAYS = S.REPRESENTATIVE_DAYS
# `pd.Timedelta(days=N)` 는 이 pandas 에서 DeprecationWarning 이다 — 단위를 명시한다.
SIM_END = SIM_START + pd.Timedelta(SIM_DAYS - 1, unit="D")
SIM_BASIS = f"대표 기간 {SIM_START:%Y-%m-%d}~{SIM_END:%Y-%m-%d}"

# 픽업 재현 수준(A-19·A-20). 절감폭의 절대 크기가 이만큼 축소돼 있다.
PICKUP_FIDELITY_PCT = 53

# ─────────────────────────────────────────────────────────────
# 색칠 지표 — 사이드바에서 고른다.
#
# fold  기준선. 숫자면 절대값, "p75"/"p25" 면 폴리곤 분포의 백분위.
#       아래는 한 색으로 접고 위만 농도를 준다 — 시선을 좁히는 장치(명세 4절).
# worse "high" 면 클수록 나쁨, "low" 면 작을수록 나쁨(공급 지표).
#
# 평균 대기만 절대값(45분)이다. 시뮬 before/after 에서 개선이 보여야 하는데
# 백분위는 무엇을 넣든 항상 상위 25%가 빨강이라 개선이 드러나지 않는다.
# 진단과 시뮬을 같은 기준으로 읽으려면 절대값이어야 한다.
# 나머지는 근거 있는 절대 임계가 없어 상위 25%(공급 지표는 하위 25%)를 쓴다.
#
# ⚠ 45분과 p75 의 일치는 깨졌다(특장차 한정 모집단 · A-16). 구 모집단에서는 45분이
# 마침 p75(45.5분)라 네 지표가 같은 논리로 정렬됐는데, 지금은 p75 = 46.80분이고
# 45분은 p67.3 지점이다. 평균 대기만 빨강이 137개(32.7%)로 다른 셋(105개 · 25.1%)보다
# 넓다. 그래도 절대값을 유지하는 이유는 위 문단 그대로다 — 일치는 편의였지 근거가
# 아니었다. 좁히려면 RED_THRESHOLD_MIN 만 바꾸되 진단과 시뮬의 자가 달라진다.
#
# 이용률은 넣지 않는다. 낮은 이용률이 미충족 수요인지 낮은 수요인지 자료로
# 가릴 수 없어 방향을 정할 수 없다. 방향을 임의로 정하면 도구가 답을 내는 게
# 된다(명세 4절). 패널에는 그대로 표시한다.
# ─────────────────────────────────────────────────────────────

METRICS = {
    "wait_total_mean": dict(label="평균 대기", unit="분", digits=1,
                            fold=RED_THRESHOLD_MIN, worse="high"),
    "long_wait_ratio": dict(label="60분 초과 비율", unit="%", digits=1,
                            fold="p75", worse="high", scale=100),
    "cancel_abandoned_ratio": dict(label="기다리다 포기", unit="%", digits=1,
                                   fold="p75", worse="high", scale=100),
    "vehicles_3km": dict(label="3km 내 차량", unit="대", digits=0,
                         fold="p25", worse="low"),
}
DEFAULT_METRIC = "wait_total_mean"

# 폴리곤 단위로 가중평균해 둘 지표(지도 색칠용)
POLY_COLS = list(METRICS)


# ─────────────────────────────────────────────────────────────
# 로딩 — 전부 캐시한다(명세 7절)
# ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_metrics() -> pd.DataFrame:
    """동별 지표 432행."""
    df = pd.read_csv(OUTPUTS / "dong_metrics.csv")
    df["base"] = df["dong_canon"].map(load._base_dong)
    return df


@st.cache_data(show_spinner=False)
def load_demand() -> pd.DataFrame:
    """동 × 시간대 × 평일/주말 콜 수."""
    return pd.read_parquet(OUTPUTS / "dong_demand_matrix.parquet")


@st.cache_data(show_spinner=False)
def load_boundary():
    """단순화한 서울 동 경계. (geojson dict, 속성표) 로 돌려준다.

    GeoDataFrame 은 캐시 직렬화가 무거워 dict + DataFrame 으로 쪼갠다.
    """
    gdf = load.load_dong(with_boundary=True).copy()
    gdf["geometry"] = gdf.geometry.simplify(SIMPLIFY_TOL, preserve_topology=True)
    gdf["base"] = gdf["dong_canon"].map(load._base_dong)
    gj = json.loads(gdf.to_json())
    for feat, cd in zip(gj["features"], gdf["adm_cd2"]):
        feat["id"] = cd
    attrs = pd.DataFrame({
        "adm_cd2": gdf["adm_cd2"].tolist(),
        "adm_nm": gdf["adm_nm"].tolist(),
        "gu": gdf["gu"].tolist(),
        "dong_canon": gdf["dong_canon"].tolist(),
        "base": gdf["base"].tolist(),
    })
    return gj, attrs


@st.cache_data(show_spinner=False)
def load_depots() -> pd.DataFrame:
    return load.load_depots()


@st.cache_data(show_spinner=False)
def load_candidates() -> pd.DataFrame:
    """지도에 올릴 후보 — **동에 실제로 배정된 곳만**.

    옥외 후보는 348곳이지만 시뮬이 쓰는 건 `candidates.assign_dong_candidates` 가
    동에 배정한 244곳이다. 348곳을 다 찍으면 **지도가 시뮬 입력과 다른 것을 보여준다**
    — 실무자는 점 하나하나를 "여기에 놓을 수 있다"로 읽는데, 배정되지 않은 104곳은
    어느 동의 거점도 되지 않는다.

    한 후보가 여러 동에 배정될 수 있어(대체 배정) 배정 동 목록을 함께 붙인다.
    `dong_candidates.csv` 가 없으면 348곳 전체로 물러난다 — 지도가 비는 것보다 낫다.

    **`load.load_candidates()` 의 attrs 를 그대로 물려준다.** 사이드바가
    `n_pool`·`n_excluded_indoor_mixed` 를 읽는데, `merge` 는 attrs 를 버려서
    한 번 여기서 잃은 적이 있다(KeyError: 'n_pool'). 아래 `attrs.update` 가
    그 자리다 — 지우면 사이드바가 다시 죽는다.

    반환: load.load_candidates() 컬럼 + assigned_dongs(문자열), n_assigned_dongs
    """
    cand = load.load_candidates()
    path = OUTPUTS / "dong_candidates.csv"
    if not path.exists():
        cand = cand.assign(assigned_dongs="", n_assigned_dongs=0)
        cand.attrs["n_outdoor"] = len(cand)
        cand.attrs["assigned_only"] = False
        return cand

    a = pd.read_csv(path)
    a = a[a["is_assigned"] & a["cand_id"].notna()]
    per = (a.assign(label=a["gu"] + " " + a["dong_canon"])
            .groupby("cand_id")["label"]
            .agg(assigned_dongs=lambda s: " · ".join(sorted(s)),
                 n_assigned_dongs="size"))

    n_outdoor = len(cand)
    out = cand.merge(per, left_on="cand_id", right_index=True, how="inner")
    out.attrs.update(cand.attrs)          # merge 가 버린 원본 attrs 를 되살린다
    out.attrs["n_outdoor"] = n_outdoor
    out.attrs["assigned_only"] = True
    return out.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 시뮬 결과 — 3페이지
# ─────────────────────────────────────────────────────────────

# 화면에 쓰지 않는 열. 등급은 계산만 남기고 표시하지 않기로 했다 —
# 판단을 대신하는 표시라 도구의 성격과 어긋나고, 244곳에서 [상] 4곳 중 3곳이
# 표본 부족이라 최고 등급이 가장 좁은 지역을 가리켰다.
# 근거는 docs/model_flow.md 「등급은 계산하되 표시하지 않는다」.
REF_PREFIX = "ref_"


@st.cache_data(show_spinner=False)
def load_placement() -> pd.DataFrame:
    """후보별 시뮬 결과 244행. **`ref_*`(등급) 열은 아예 떨어뜨린다.**

    화면에서 안 쓰는 것으로 충분하지 않다 — 열이 남아 있으면 다음 사람이
    집어 쓴다. 여기서 자르면 3페이지 코드가 등급에 손댈 방법이 없다.

    반환: cand_id, cand_name, gu, dongs, n_dong_scope, n_calls_scope,
          before, delta, rate_pct, rate_sd, rate_lo, rate_hi, interval,
          sample_ok, total_delta_min, n_overlap (+ after 파생)
    """
    path = OUTPUTS / "placement_grades.csv"
    if not path.exists():
        return pd.DataFrame()
    g = pd.read_csv(path, encoding="utf-8-sig")
    g = g[[c for c in g.columns if not c.startswith(REF_PREFIX)]].copy()
    g["after"] = g["before"] + g["delta"]
    g["half"] = (g["rate_hi"] - g["rate_lo"]) / 2
    return g


@st.cache_data(show_spinner=False)
def load_placement_seeds() -> pd.DataFrame:
    """시드별 원값 732행. 후보 하나의 시드 3회를 그대로 보여줄 때 쓴다.

    평균 하나만 보이면 σ 가 어디서 왔는지 알 수 없다 — 원값 3개를 열어 두면
    "이 후보는 시드에 따라 이만큼 흔들린다"를 직접 확인할 수 있다.
    """
    path = OUTPUTS / "placement_eval.csv"
    if not path.exists():
        return pd.DataFrame()
    ev = pd.read_csv(path, encoding="utf-8-sig")
    return ev.drop_duplicates(["cand_id", "seed"], keep="first")


@st.cache_data(show_spinner=False)
def placement_scope() -> dict:
    """후보 → (3km 영향권 폴리곤, 담당 동 폴리곤). 둘 다 adm_cd2 집합이다.

    **영향권은 저장돼 있지 않아 다시 계산한다.** `placement_eval.csv` 에는
    범위 안 동 **수**(`n_dong_scope`)만 있고 어느 동인지는 없다. `evaluate`
    의 함수를 그대로 불러 같은 반경·같은 좌표로 다시 잡는다 — 규칙을 여기
    베껴 쓰면 반경이 바뀔 때 지도와 숫자가 어긋난다. 244곳 전부에서
    `n_dong_scope` 와 일치하는 것을 확인했다(tests/test_dashboard.py).

    담당 동은 `dong_candidates.csv` 의 배정 결과다. 영향권과 다르다 —
    영향권은 "3km 안에 드는 동", 담당은 "이 후보를 거점으로 배정받은 동"이다.

    **폴리곤 수는 동 수보다 적을 수 있다.** 경계 파일(2023)과 콜 원본의 동
    구분이 달라 ⑴ 대응 경계가 없는 지표 행이 7개 있고 ⑵ 개편으로 여러 행이 한
    경계에 묶인다(신당1~6동 → 신당동 등 4곳). 244곳 중 66곳이 여기 걸린다.
    그래서 `n_dong`(진짜 영향권 동 수)과 `polys`(그릴 수 있는 경계)를 **따로**
    돌려준다 — 지도가 17개를 칠하면서 "18개 동"이라고 적으면 안 된다.
    같은 어긋남을 2페이지는 `orphan_rows` 로 이미 알리고 있다(명세 5·9절).

    반환: {cand_id: {polys, assigned, lat, lon, n_dong, n_unmapped}}
    """
    ac = OUTPUTS / "dong_candidates.csv"
    if not ac.exists():
        return {}
    a = pd.read_csv(ac)
    a = a[a["is_assigned"] & a["cand_id"].notna()]

    # 지표 행 → 폴리곤. resolve_polygons 의 3단 매칭 결과를 거꾸로 뒤집는다.
    poly = resolve_polygons()
    metrics_df = load_metrics()
    key_of = list(zip(metrics_df["gu"], metrics_df["dong_canon"]))
    codes = poly["adm_cd2"].tolist()
    poly_of_key: dict = {}
    for pi, members in enumerate(poly["members"]):
        for j in members:
            poly_of_key[key_of[j]] = codes[pi]
    known = set(codes)

    dong_xy = E.dong_coords()
    out = {}
    for cid, grp in a.groupby("cand_id"):
        r = grp.iloc[0]
        keys = E.scope_members(float(r["cand_lat"]), float(r["cand_lon"]), dong_xy)
        polys = {poly_of_key[k] for k in keys if k in poly_of_key}
        # 담당 동의 adm_cd2 는 int 로 실려 있다 — 경계 쪽은 문자열이다
        assigned = {str(x) for x in grp["adm_cd2"]} & known
        out[int(cid)] = {
            "polys": sorted(polys), "assigned": sorted(assigned),
            "lat": float(r["cand_lat"]), "lon": float(r["cand_lon"]),
            "n_dong": len(keys),
            "n_unmapped": len(keys) - len(polys),
        }
    return out


def placement_row(cand_id: int):
    """후보 하나의 결과 행. 없으면 None — 결과가 없는 후보도 지도에는 있다."""
    g = load_placement()
    if g.empty:
        return None
    hit = g[g["cand_id"] == cand_id]
    return hit.iloc[0] if len(hit) else None


def candidate_options(sort_key: str) -> list:
    """사이드바 후보 목록 — (cand_id, 표시문자열) 을 정렬해 돌려준다.

    **순위 번호를 붙이지 않는다.** 정렬은 훑어보기 위한 것이지 서열이 아니다 —
    개선율 1위 마천동1 은 다른 24곳과 대비 구간이 겹쳐 단독 1위라 할 수 없다.
    번호를 붙이면 그 사실이 지워진다.

    **개선율과 총절감을 라벨에 함께 적는다.** 정렬 키만 보이면 그 축이 유일한
    기준인 것처럼 읽힌다 — 둘은 순위 상관 +0.32 로 다른 것을 잰다.
    """
    g = load_placement()
    if g.empty:
        return []
    col = "total_delta_min" if sort_key == "total" else "rate_pct"
    g = g.sort_values(col)
    out = []
    for r in g.itertuples():
        flag = "" if r.sample_ok else "  ※표본부족"
        out.append((int(r.cand_id),
                    f"{r.cand_name[:22]} ({r.gu}) · {r.rate_pct:+.2f}% · "
                    f"{r.total_delta_min:,.0f}분{flag}"))
    return out


# ─────────────────────────────────────────────────────────────
# 경계 ↔ 지표 매칭 — load.build_dong_lookup 의 3단 규칙을 폴리곤 방향으로
# 적용한다. 규칙과 근거는 명세 5절.
# ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def resolve_polygons() -> pd.DataFrame:
    """폴리곤 1행 = 지도에 칠할 단위.

    반환: adm_cd2, adm_nm, gu, dong_canon, match_type, members(지표 행 인덱스),
          member_names, n_served, is_reliable, has_metrics + POLY_COLS 가중평균
    """
    df = load_metrics()
    _, attrs = load_boundary()

    midx = {(g, d): i for i, (g, d) in enumerate(zip(df["gu"], df["dong_canon"]))}
    assign: dict = {}
    kind: dict = {}
    used: set = set()

    # 1단계 exact
    for pi, (gu, dc) in enumerate(zip(attrs["gu"], attrs["dong_canon"])):
        j = midx.get((gu, dc))
        if j is not None:
            assign[pi] = [j]
            kind[pi] = "exact"
            used.add(j)

    # 2단계 manual — 행정구역 개편으로 이름이 바뀐 동
    poly_at = {(g, d): pi
               for pi, (g, d) in enumerate(zip(attrs["gu"], attrs["dong_canon"]))}
    for (gu, src), target in load.MANUAL_DONG.items():
        j = midx.get((gu, load.canon_dong(src)))
        if j is None or j in used:
            continue
        pi = poly_at.get((gu, load.canon_dong(target)))
        if pi is None:
            continue
        assign.setdefault(pi, []).append(j)
        kind[pi] = "exact+manual" if kind.get(pi) == "exact" else "manual"
        used.add(j)

    # 3단계 approx — 기본명 그룹의 '잔여' 행만. exact 로 소비된 행을 다시 쓰면
    # 없는 데이터를 지어낸다(명세 5절).
    leftover = df[~df.index.isin(used)]
    for pi, (gu, b) in enumerate(zip(attrs["gu"], attrs["base"])):
        if pi in assign:
            continue
        cand = leftover[(leftover["gu"] == gu) & (leftover["base"] == b)]
        if len(cand):
            assign[pi] = list(cand.index)
            kind[pi] = "approx"
            used.update(cand.index)

    rows = []
    for pi in range(len(attrs)):
        a = attrs.iloc[pi]
        js = assign.get(pi, [])
        rec = {
            "adm_cd2": a["adm_cd2"], "adm_nm": a["adm_nm"], "gu": a["gu"],
            "dong_canon": a["dong_canon"],
            "match_type": kind[pi] if js else "none",
            "members": js, "member_names": [],
            "n_served": 0, "is_reliable": True, "has_metrics": bool(js),
        }
        if js:
            sub = df.loc[js]
            w = sub["n_served"].clip(lower=0)
            tot = w.sum()
            rec["member_names"] = sub["dong"].tolist()
            rec["n_served"] = int(tot)
            rec["is_reliable"] = bool((sub["n_served"] >= 100).all())
            for col in POLY_COLS:
                # 승차 완료 가중 평균 — 24건 동과 4,000건 동을 같은 무게로 섞지 않는다
                rec[col] = ((sub[col] * w).sum() / tot) if tot else float("nan")
        else:
            for col in POLY_COLS:
                rec[col] = float("nan")
        rows.append(rec)

    out = pd.DataFrame(rows)
    out["wait"] = out["wait_total_mean"]        # 요약·범례에서 쓰는 별칭
    out.attrs["orphan_rows"] = df[~df.index.isin(used)][
        ["gu", "dong", "n_served", "wait_total_mean"]].to_dict("records")
    return out


@st.cache_data(show_spinner=False)
def seoul_summary() -> dict:
    """상단 요약. 개별 동 값을 읽을 때의 비교 기준이 된다."""
    df = load_metrics()
    poly = resolve_polygons()
    rel = df[df["is_reliable"]]
    return {
        "mean_wait": float(df["wait_total_mean_seoul_mean"].iloc[0]),
        "n_red": int((poly["wait"] >= RED_THRESHOLD_MIN).sum()),
        "n_colored": int(poly["has_metrics"].sum()),
        # 지역 지니 — 승차 100건 이상 동, 동 균등가중(가정 A-12)
        "gini": float(M.gini(rel["wait_total_mean"])),
        "n_reliable": int(len(rel)),
    }


def metric_fold(poly: pd.DataFrame, key: str) -> float:
    """색을 접는 기준선(원 단위). 절대값이거나 폴리곤 분포의 백분위다.

    백분위는 **색칠 대상 폴리곤 419개** 위에서 잡는다. 지표 행 432 기준이
    아니라 실제로 지도에 칠해지는 모집단이라야 "빨강 25%"가 성립한다.
    """
    spec = METRICS[key]
    fold = spec["fold"]
    if isinstance(fold, (int, float)):
        return float(fold)
    q = float(str(fold).lstrip("p")) / 100.0
    vals = poly.loc[poly["has_metrics"], key].dropna()
    return float(vals.quantile(q))


@st.cache_data(show_spinner=False)
def metric_summary() -> dict:
    """지표별 (기준선, 빨강 폴리곤 수). 사이드바 범례에 쓴다."""
    poly = resolve_polygons()
    out = {}
    for key, spec in METRICS.items():
        sc = spec.get("scale", 1)
        fold = metric_fold(poly, key) * sc
        vals = poly.loc[poly["has_metrics"], key].dropna() * sc
        n_red = int((vals >= fold).sum()) if spec["worse"] == "high" \
            else int((vals <= fold).sum())
        out[key] = (fold, n_red)
    return out


# ─────────────────────────────────────────────────────────────
# 색
# ─────────────────────────────────────────────────────────────

def fold_colorscale(vmin: float, vmax: float, fold: float) -> list:
    """기준선 미만 단일 초록, 이상은 옅은→진한 빨강. 하드 스톱(명세 4절)."""
    if vmax <= vmin:
        return [[0.0, GREEN], [1.0, GREEN]]
    t = (fold - vmin) / (vmax - vmin)
    t = min(max(t, 0.0), 1.0)
    if t <= 0:
        return [[0.0, RED_LIGHT], [1.0, RED_DARK]]
    if t >= 1:
        return [[0.0, GREEN], [1.0, GREEN]]
    eps = 1e-6
    return [[0.0, GREEN], [t - eps, GREEN], [t, RED_LIGHT], [1.0, RED_DARK]]


def grey_colorscale() -> list:
    """채도를 버리고 명암만 남긴다 — 물러나되 사라지지는 않게."""
    return [[0.0, GREY_LIGHT], [1.0, GREY_DARK]]


def metric_z(poly: pd.DataFrame, key: str):
    """(z, zmin, zmax, colorscale, 표시용 원값).

    공급 지표(worse='low')는 부호를 뒤집어 같은 스케일 로직을 그대로 쓴다 —
    작을수록 빨강이 된다.
    """
    spec = METRICS[key]
    has = poly[poly["has_metrics"]]
    raw = has[key].astype(float)
    scale = spec.get("scale", 1)
    fold = metric_fold(poly, key) * scale
    vals = raw * scale
    if spec["worse"] == "low":
        z = (-vals).tolist()
        zmin, zmax = float(-vals.max()), float(-vals.min())
        cs = fold_colorscale(zmin, zmax, -fold)
    else:
        z = vals.tolist()
        zmin, zmax = float(vals.min()), float(vals.max())
        cs = fold_colorscale(zmin, zmax, fold)
    return z, zmin, zmax, cs, vals.tolist(), fold


# ─────────────────────────────────────────────────────────────
# 지도
# ─────────────────────────────────────────────────────────────

def build_map(poly: pd.DataFrame, selected, show_depots: bool,
              show_cands: bool, metric_key: str) -> go.Figure:
    """지도 figure.

    geojson·locations 를 rerun 마다 동일하게 유지해 실제로 달라지는 것이
    z(색 배열)뿐이도록 만든다. uirevision 이 붙어 있으면 Plotly 가 이를
    갱신으로 처리해 카메라를 유지하고 transition 을 적용한다(명세 7절).
    """
    gj, _ = load_boundary()
    has = poly[poly["has_metrics"]]
    spec = METRICS[metric_key]
    z, zmin, zmax, cs, shown, _fold = metric_z(poly, metric_key)

    locs = has["adm_cd2"].tolist()
    names = has["adm_nm"].tolist()
    unit, digits = spec["unit"], spec["digits"]
    cdata = [[n, v] for n, v in zip(names, shown)]
    htmpl = (f"%{{customdata[0]}}<br>{spec['label']} "
             f"%{{customdata[1]:,.{digits}f}}{unit}<extra></extra>")

    fig = go.Figure()

    # 매칭 없는 폴리곤 — 빼지 않는다. 구멍이 뚫리면 '값이 좋은 동'으로 읽힌다.
    none = poly[~poly["has_metrics"]]
    if len(none):
        fig.add_trace(go.Choroplethmap(
            geojson=gj, locations=none["adm_cd2"].tolist(),
            z=[0] * len(none), colorscale=[[0, NO_MATCH], [1, NO_MATCH]],
            showscale=False, marker_line_width=0.4, marker_line_color="#C9C6C0",
            hovertext=none["adm_nm"].tolist(),
            hovertemplate="%{hovertext}<br>지표 매칭 없음<extra></extra>",
            name="지표 매칭 없음",
        ))

    if selected is None:
        fig.add_trace(go.Choroplethmap(
            geojson=gj, locations=locs, z=z, zmin=zmin, zmax=zmax, colorscale=cs,
            marker_line_width=0.4, marker_line_color="#FFFFFF",
            marker_opacity=0.88, customdata=cdata, hovertemplate=htmpl,
            showscale=False, name=spec["label"],
        ))
    else:
        # 회색조 바닥 + 선택 동만 컬러.
        # 선택 외 z 를 None 으로 두면 색이 칠해지지 않는다(트레이스는 유지).
        fig.add_trace(go.Choroplethmap(
            geojson=gj, locations=locs, z=z, zmin=zmin, zmax=zmax,
            colorscale=grey_colorscale(), showscale=False,
            marker_line_width=0.4, marker_line_color="#FFFFFF",
            marker_opacity=0.75, customdata=cdata, hovertemplate=htmpl,
            name="그 외 동",
        ))
        sel_z = [v if loc == selected else None for loc, v in zip(locs, z)]
        fig.add_trace(go.Choroplethmap(
            geojson=gj, locations=locs, z=sel_z, zmin=zmin, zmax=zmax,
            colorscale=cs, showscale=False, marker_line_width=0,
            marker_opacity=0.95, customdata=cdata, hovertemplate=htmpl,
            name="선택 동",
        ))

    # 후보를 차고지보다 먼저 그린다 — 겹칠 때 현행 차고지가 위로 와야
    # "이미 있는 곳"과 "놓을 수 있는 곳"이 구별된다.
    if show_cands:
        cand = load_candidates()
        # customdata 첫 칸의 태그로 클릭 이벤트에서 후보 점을 구분한다.
        # 코로플레스 점은 location 을 갖고 이 태그가 없다.
        # 실가용은 시영에만 있다 — 없는 건 -1 로 넘겨 패널에서 행을 뺀다
        # (None 을 섞으면 plotly customdata 가 문자열로 눌린다).
        # 마지막 칸이 cand_id 다 — 클릭하면 이 값으로 3페이지를 연다.
        cdata = [[CAND_TAG, n, int(c), s, g, -1 if pd.isna(a) else int(a), d,
                  int(i)]
                 for n, c, s, g, a, d, i in zip(
                     cand["name"], cand["capacity"], cand["source"], cand["gu"],
                     cand["capacity_available"], cand["assigned_dongs"],
                     cand["cand_id"])]
        fig.add_trace(go.Scattermap(
            lat=cand["lat"], lon=cand["lon"], mode="markers",
            marker=dict(size=7, color=CAND_COLOR),
            customdata=cdata,
            hovertemplate=("%{customdata[1]}<br>%{customdata[2]:,}면 · "
                           "%{customdata[3]} · %{customdata[4]}"
                           "<br>배정: %{customdata[6]}<extra></extra>"),
            name="후보 주차장",
        ))

    if show_depots:
        dep = load_depots()
        fig.add_trace(go.Scattermap(
            lat=dep["lat"], lon=dep["lon"], mode="markers",
            marker=dict(size=9, color=DEPOT_COLOR),
            hovertext=[f"{n} · {c}대" for n, c in zip(dep["name"], dep["capacity"])],
            hovertemplate="%{hovertext}<extra></extra>", name="차고지",
        ))

    fig.update_layout(
        map=dict(style=MAP_STYLE, center=MAP_CENTER, zoom=MAP_ZOOM),
        margin=dict(l=0, r=0, t=0, b=0),
        height=720,
        uirevision="constant",           # rerun 시 줌·팬 유지 — 가장 크게 기여
        transition=dict(duration=400, easing="cubic-in-out"),
        paper_bgcolor=BG,
        showlegend=False,
        font=dict(color=INK),
        clickmode="event+select",
    )
    return fig


def improve_color(rate: float, lo: float, hi: float) -> str:
    """개선율 → 초록 농도. lo·hi 는 **244곳 전체** 범위다(후보 간 비교용)."""
    t = 0.0 if hi <= lo else (abs(rate) - lo) / (hi - lo)
    t = min(max(t, 0.0), 1.0)

    def mix(a: str, b: str) -> str:
        ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))

    return mix(IMPROVE_LIGHT, IMPROVE_DARK)


def build_sim_map(cand_id: int, row) -> go.Figure:
    """한 후보의 3km 영향권 지도.

    **색은 후보 하나 안에서 평평하다 — 그게 데이터의 한계다.** `placement_eval.csv`
    의 before/after 는 3km 범위 **전체를 콜 가중 평균한 값 하나**이고
    (`evaluate.scope_pickup`), 동별 before/after 는 산출돼 있지 않다. 그래서
    영향권 동들을 서로 다른 농도로 칠하면 **없는 차이를 지어내는 것**이 된다.
    한 색으로 칠하고 그 값을 범례에 적는다 — 지도가 답하는 것은 "얼마나
    줄어드는가"가 아니라 **"어디까지 닿는가"** 다.

    농도는 **후보끼리** 비교하라고 둔다. 244곳의 |개선율| 범위 위에서 잡으므로
    다른 후보로 넘어가면 진하기가 달라진다.

    담당 동은 테두리로 가른다. 영향권(3km 안)과 담당(거점으로 배정된 동)은
    다른 개념이고, 한 후보가 최대 5개 동을 맡는다.

    `uirevision` 은 **후보마다 다르다.** "constant" 로 두면 후보를 바꿔도
    카메라가 그대로라 새 후보가 화면 밖에 있을 수 있다. 같은 후보 안의
    rerun 에서는 값이 같아 줌·팬이 유지된다.
    """
    gj, _ = load_boundary()
    poly = resolve_polygons()
    sc = placement_scope().get(cand_id, {})
    scope_set = set(sc.get("polys", []))
    assigned_set = set(sc.get("assigned", []))
    lat, lon = sc.get("lat"), sc.get("lon")

    g = load_placement()
    lo, hi = g["rate_pct"].abs().min(), g["rate_pct"].abs().max()
    fill = improve_color(float(row["rate_pct"]), lo, hi)

    locs = poly["adm_cd2"].tolist()
    names = poly["adm_nm"].tolist()
    fig = go.Figure()

    # ① 바닥 — 영향권 밖. 빼지 않는다. 구멍이 뚫리면 서울이 아닌 것으로 읽힌다.
    fig.add_trace(go.Choroplethmap(
        geojson=gj, locations=locs, z=[0] * len(locs),
        colorscale=[[0, OUT_SCOPE], [1, OUT_SCOPE]], showscale=False,
        marker_line_width=0.4, marker_line_color="#CFCCC6",
        hovertext=names, hovertemplate="%{hovertext}<br>영향권 밖<extra></extra>",
        name="영향권 밖",
    ))

    # ② 영향권 — 한 색. 값은 범례에 적는다.
    rate = float(row["rate_pct"])
    before, after = float(row["before"]), float(row["after"])
    z_scope = [0 if loc in scope_set else None for loc in locs]
    fig.add_trace(go.Choroplethmap(
        geojson=gj, locations=locs, z=z_scope, zmin=0, zmax=1,
        colorscale=[[0, fill], [1, fill]], showscale=False,
        marker_line_width=0.5, marker_line_color="#FFFFFF",
        marker_opacity=0.92, hovertext=names,
        hovertemplate=(f"%{{hovertext}}<br>3km 영향권 · 픽업 {before:.2f} → "
                       f"{after:.2f}분 ({rate:+.2f}%)<extra></extra>"),
        name="3km 영향권",
    ))

    # ③ 담당 동 — 테두리만 강하게. 채움은 ②와 같아야 "영향권 안"이 유지된다.
    if assigned_set:
        z_as = [0 if loc in assigned_set else None for loc in locs]
        fig.add_trace(go.Choroplethmap(
            geojson=gj, locations=locs, z=z_as, zmin=0, zmax=1,
            colorscale=[[0, fill], [1, fill]], showscale=False,
            marker_line_width=2.0, marker_line_color=ASSIGNED_LINE,
            marker_opacity=1.0, hovertext=names,
            hovertemplate="%{hovertext}<br><b>담당 동</b><extra></extra>",
            name="담당 동",
        ))

    # ④ 후보 위치
    if lat is not None:
        fig.add_trace(go.Scattermap(
            lat=[lat], lon=[lon], mode="markers",
            marker=dict(size=13, color=CAND_COLOR),
            hovertext=[f"{row['cand_name']} · {row['gu']}"],
            hovertemplate="%{hovertext}<extra></extra>", name="후보",
        ))

    center = ({"lat": lat, "lon": lon} if lat is not None else MAP_CENTER)
    fig.update_layout(
        map=dict(style=MAP_STYLE, center=center, zoom=11.4),
        margin=dict(l=0, r=0, t=0, b=0), height=720,
        uirevision=f"sim-{cand_id}",     # 후보가 바뀌면 카메라를 다시 잡는다
        transition=dict(duration=400, easing="cubic-in-out"),
        paper_bgcolor=BG, showlegend=False, font=dict(color=INK),
    )
    return fig


# ─────────────────────────────────────────────────────────────
# 패널 조각
# ─────────────────────────────────────────────────────────────

def fmt(v, unit: str = "", digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{digits}f}{unit}"


def headline(label: str, value: str, gu: str = None, seoul: str = None) -> str:
    cmp_html = (f'<div class="cmp">구 {gu} · 서울 {seoul}</div>'
                if gu is not None else "")
    return (f'<div class="head-metric"><div class="hlabel">{label}</div>'
            f'<div class="hvalue">{value}</div>{cmp_html}</div>')


def rows_html(items: list) -> str:
    out = []
    for label, value in items:
        out.append(f'<div class="metric"><div class="mlabel">{label}</div>'
                   f'<div class="mvalue">{value}</div></div>')
    return "".join(out)


def note_html(text: str) -> str:
    return f'<div class="note">{text}</div>' if text else ""


def cand_spec_rows(cand_id: int) -> None:
    """후보 제원 — 면수·소스·실가용. 3페이지 「후보 제원」에 접어 둔다.

    **면수가 시뮬 입력이고 실가용은 참고값이다.** 실측 잔여면은 시영 65곳에만
    있어서 그것을 용량으로 쓰면 후보 간 비교에서 자가 섞인다. 시영일 때만
    한 줄 더 붙여 "총 면수만 보면 안 된다"를 화면에서 알 수 있게 한다.

    예전에는 지도에서 후보를 클릭했을 때 오른쪽에 뜨는 패널이었다. 후보 클릭이
    3페이지로 가게 되면서 이리로 옮겼다 — **사이드바 목록으로 들어와도 같은
    것을 봐야 하므로** 클릭 customdata 가 아니라 cand_id 로 다시 찾는다.
    """
    cand = load_candidates()
    hit = cand[cand["cand_id"] == cand_id]
    if not len(hit):
        st.markdown(note_html("후보 제원을 찾을 수 없습니다."), unsafe_allow_html=True)
        return
    r = hit.iloc[0]
    available = r["capacity_available"]
    rows = [("면수", f"{int(r['capacity']):,}면"), ("소스", r["source"]),
            ("자치구", r["gu"])]
    note = "옥외 후보입니다. 시뮬 입력 용량은 이 총 면수입니다."
    if pd.notna(available):
        rows.insert(1, ("실가용 면수 (참고)", f"{int(available):,}면"))
        note = ("옥외 후보입니다. <b>시뮬 입력은 총 면수</b>이고, 실가용 면수는 "
                "정보공개청구 <b>피크시간 잔여구획</b>의 일별 중앙값 — 그 날 가장 "
                "붐빈 1시간에 실제로 비어 있던 면입니다. 시영 65곳에만 있어 "
                "용량으로는 쓰지 않고, 시뮬 뒤 후보를 좁힐 때 참고합니다. "
                "하루 중 최악값이라 야간 여력은 이보다 큽니다.")
    st.markdown(rows_html(rows) + note_html(note), unsafe_allow_html=True)


def dong_panel(row: pd.Series, df: pd.DataFrame) -> None:
    """선택 동 세부지표.

    핵심 5개(구·서울 비교가 붙는 것)만 항상 보이고 나머지는 접는다.
    """
    members = row["members"]
    sub = df.loc[members]
    w = sub["n_served"].clip(lower=0)
    tot = float(w.sum())

    def wavg(col):
        if col not in sub.columns or tot == 0:
            return float("nan")
        return (sub[col] * w).sum() / tot

    def has_cmp(col):
        g = f"{col}_gu_mean"
        return g in sub.columns and pd.notna(sub.iloc[0][g])

    def pct(col):
        v = wavg(col)
        return v * 100 if pd.notna(v) else v

    st.markdown(f'<div class="dong-name">{row["adm_nm"]}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="dong-sub">승차 완료 {int(tot):,}건 · {BASIS}</div>',
                unsafe_allow_html=True)

    # 값이 흔들리는 동은 숨기지 않고 사유를 문장으로 붙인다(명세 6절)
    warns = []
    if not row["is_reliable"]:
        warns.append(f"표본 {int(tot):,}건으로 값이 불안정할 수 있습니다.")
    if row["match_type"] == "approx":
        warns.append("경계 개편으로 근사 매칭된 동입니다 "
                     f"(지표: {' · '.join(row['member_names'])}).")
    elif row["match_type"] in ("manual", "exact+manual") and len(members) > 1:
        warns.append(f"개편 전 {' · '.join(row['member_names'])} 를 합산한 값입니다.")
    for msg in warns:
        st.markdown(f'<div class="warn">{msg}</div>', unsafe_allow_html=True)

    # ── 핵심 5개 — 항상 보인다
    cards = []
    cards.append(headline(
        "총 대기 평균", fmt(wavg("wait_total_mean"), "분"),
        *((fmt(wavg("wait_total_mean_gu_mean"), "분"),
           fmt(wavg("wait_total_mean_seoul_mean"), "분"))
          if has_cmp("wait_total_mean") else (None, None))))
    cards.append(headline(
        "60분 초과 비율", fmt(pct("long_wait_ratio"), "%"),
        *((fmt(pct("long_wait_ratio_gu_mean"), "%"),
           fmt(pct("long_wait_ratio_seoul_mean"), "%"))
          if has_cmp("long_wait_ratio") else (None, None))))
    cards.append(headline(
        "기다리다 포기", fmt(pct("cancel_abandoned_ratio"), "%"),
        *((fmt(pct("cancel_abandoned_ratio_gu_mean"), "%"),
           fmt(pct("cancel_abandoned_ratio_seoul_mean"), "%"))
          if has_cmp("cancel_abandoned_ratio") else (None, None))))
    cards.append(headline(
        "3km 내 차량", fmt(wavg("vehicles_3km"), "대", 0),
        *((fmt(wavg("vehicles_3km_gu_mean"), "대", 0),
           fmt(wavg("vehicles_3km_seoul_mean"), "대", 0))
          if has_cmp("vehicles_3km") else (None, None))))

    ur = wavg("usage_rate")
    cards.append(headline(
        "이용률", fmt(ur, "", 2),
        *((fmt(wavg("usage_rate_gu_mean"), "", 2),
           fmt(wavg("usage_rate_seoul_mean"), "", 2))
          if has_cmp("usage_rate") else (None, None))))
    st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)

    # 이용률 사유는 카드 바로 아래 한 줄로(명세 6절)
    if pd.isna(ur):
        st.markdown(note_html("이용률 — 등록 장애인 통계에 이 동의 값이 '-' 로 들어와 "
                              "낼 수 없습니다."), unsafe_allow_html=True)
    elif bool(sub.iloc[0].get("usage_is_grouped", False)):
        st.markdown(note_html("이용률 — 인접 동과 합산된 그룹 값입니다."),
                    unsafe_allow_html=True)

    # ── 나머지는 접는다
    with st.expander("대기시간 상세"):
        cells = []
        for rlab, pre in [("총", "wait_total"), ("매칭", "wait_match"),
                          ("픽업", "wait_pickup")]:
            cells.append(
                f'<tr><th>{rlab}</th>'
                f'<td>{fmt(wavg(pre + "_mean"), "")}</td>'
                f'<td>{fmt(wavg(pre + "_p50"), "")}</td>'
                f'<td>{fmt(wavg(pre + "_p90"), "")}</td></tr>')
        st.markdown(
            '<table class="grid"><thead><tr><th></th><th>평균</th><th>중앙</th>'
            f'<th>p90</th></tr></thead><tbody>{"".join(cells)}</tbody></table>'
            + note_html("단위 분. 총 = 승차−접수, 매칭 = 배차−접수, 픽업 = 승차−배차"),
            unsafe_allow_html=True)

    with st.expander("취소·완료 상세"):
        n_calls = float(sub["n_calls"].sum())
        done = (tot / n_calls * 100) if n_calls else float("nan")
        st.markdown(rows_html([
            ("완료율", fmt(done, "%")),
            ("즉시 취소", fmt(pct("cancel_immediate_ratio"), "%")),
            ("기다리다 포기", fmt(pct("cancel_abandoned_ratio"), "%")),
            ("배차 후 미승차", fmt(pct("cancel_after_assign_ratio"), "%")),
            ("판별 불가", fmt(pct("cancel_other_ratio"), "%")),
        ]) + note_html("기다리다 포기 = 배차 전 1분 초과 취소. 공급 부족의 핵심 신호"),
            unsafe_allow_html=True)

    with st.expander("콜 발생"):
        dm = load_demand()
        keys = set(zip(sub["gu"], sub["dong_canon"]))
        d = dm[[k in keys for k in zip(dm["gu"], dm["dong_canon"])]]
        n_calls = float(sub["n_calls"].sum())
        cells = [f'<tr><th>전체</th><td colspan="3">{fmt(n_calls, "건", 0)}</td></tr>']
        if len(d):
            by_p = d.groupby("period", observed=True)["n_calls"].sum()
            pv = "".join(
                f"<td>{fmt(by_p.get(p, 0), '', 0)}</td>" for p in PERIOD_ORDER)
            cells.append(f'<tr><th>시간대</th>{pv}</tr>')
            by_w = d.groupby("is_weekend")["n_calls"].sum()
            cells.append(
                f'<tr><th>평일/주말</th><td colspan="2">'
                f'{fmt(by_w.get(False, 0), "", 0)}</td>'
                f'<td colspan="2">{fmt(by_w.get(True, 0), "", 0)}</td></tr>')
        st.markdown(
            '<table class="grid"><thead><tr><th></th><th>심야</th><th>아침</th>'
            f'<th>낮</th><th>저녁</th></tr></thead><tbody>{"".join(cells)}</tbody>'
            '</table>' + note_html("단위 건. 평일/주말은 각각 앞·뒤 두 칸"),
            unsafe_allow_html=True)

    with st.expander("소요시간·속도·거리"):
        st.markdown(rows_html([
            ("차내 소요시간", fmt(wavg("ride_min_mean"), "분")),
            ("평균 속도", fmt(wavg("ride_kmh_mean"), "km/h")),
            ("평균 거리", fmt(wavg("ride_km_mean"), "km", 2)),
        ]), unsafe_allow_html=True)

    with st.expander("등록 장애인·근접도"):
        n_dis = sub["n_disabled"].sum() if sub["n_disabled"].notna().any() else float("nan")
        st.markdown(rows_html([
            ("등록 장애인", fmt(n_dis, "명", 0)),
            ("3km 내 차고지", fmt(wavg("n_depots_3km"), "곳", 0)),
        ]), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# CSS — 기본 테마를 쓰지 않는다(명세 8절)
# ─────────────────────────────────────────────────────────────

CSS = f"""
<style>
  .stApp {{ background: {BG}; color: {INK}; }}
  header[data-testid="stHeader"] {{ background: transparent; }}
  .block-container {{ padding-top: 1.4rem; padding-bottom: 1rem; max-width: 100%; }}
  section[data-testid="stSidebar"] {{ background: {PANEL_BG};
                                      border-right: 1px solid #E4E1DB; }}

  /* 제목 위계 — 앱 제목 / 지도 제목 */
  .app-title {{ font-size: 1.5rem; font-weight: 680; letter-spacing: -0.025em;
                color: {INK}; line-height: 1.15; }}
  .app-sub {{ font-size: .76rem; color: #8A867D; margin-bottom: .9rem; }}
  .map-title {{ font-size: .95rem; font-weight: 620; color: {INK};
                margin-bottom: .05rem; }}
  .map-sub {{ font-size: .74rem; color: #8A867D; margin-bottom: .5rem;
              font-variant-numeric: tabular-nums; }}

  /* 상단 요약 — st.metric 을 조용하게 */
  div[data-testid="stMetric"] {{ background: {PANEL_BG}; border: 1px solid #E4E1DB;
                                 border-radius: 8px; padding: .55rem .75rem; }}
  div[data-testid="stMetricLabel"] p {{ font-size: .72rem !important;
                                        color: #8A867D !important; }}
  div[data-testid="stMetricValue"] {{ font-size: 1.35rem !important;
                                      font-variant-numeric: tabular-nums;
                                      color: {INK} !important; }}

  /* 패널 — 좁고 스크롤, 등장은 부드럽게 */
  .panel {{
    background: {PANEL_BG}; border: 1px solid #E4E1DB; border-radius: 10px;
    padding: .9rem 1rem .5rem;
    animation: rise .32s cubic-bezier(.2,.7,.3,1);
  }}
  @keyframes rise {{
    from {{ opacity: 0; transform: translateY(8px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}

  .dong-name {{ font-size: 1.3rem; font-weight: 650; letter-spacing: -0.02em; }}
  .dong-sub  {{ font-size: .74rem; color: #7A766E; margin-bottom: .7rem;
                font-variant-numeric: tabular-nums; }}

  .warn {{ font-size: .74rem; line-height: 1.45; color: #7A4A20;
           background: #FBF2E6; border-left: 2px solid #C9975B;
           padding: .45rem .6rem; border-radius: 3px; margin-bottom: .5rem; }}

  /* 핵심 지표 카드 — 숫자가 주인공 */
  .cards {{ display: grid; grid-template-columns: 1fr 1fr; gap: .45rem;
            margin-bottom: .7rem; }}
  .head-metric {{ background: #FFFFFF; border: 1px solid #EAE7E1;
                  border-radius: 8px; padding: .5rem .6rem; }}
  .head-metric:first-child {{ grid-column: 1 / -1; }}
  .hlabel {{ font-size: .7rem; color: #8A867D; margin-bottom: .1rem; }}
  .hvalue {{ font-size: 1.25rem; font-weight: 650; color: {INK};
             font-variant-numeric: tabular-nums; line-height: 1.1; }}
  .cmp {{ font-size: .65rem; color: #9A968D; font-variant-numeric: tabular-nums;
          margin-top: .12rem; }}

  /* 접힌 블록 안의 한 줄 지표 */
  .metric {{ display: flex; align-items: baseline; justify-content: space-between;
             gap: .6rem; padding: .16rem 0; }}
  .mlabel {{ font-size: .78rem; color: #6E6A62; }}
  .mvalue {{ font-size: .95rem; font-weight: 600; color: {INK};
             font-variant-numeric: tabular-nums; }}

  /* 3×3 표 — 세로 9줄을 3줄로 */
  table.grid {{ width: 100%; border-collapse: collapse;
                font-variant-numeric: tabular-nums; }}
  table.grid th {{ font-size: .68rem; font-weight: 600; color: #8A867D;
                   text-align: right; padding: .18rem .3rem; }}
  table.grid thead th {{ border-bottom: 1px solid #E8E5DF; }}
  table.grid tbody th {{ text-align: left; color: #6E6A62; }}
  table.grid td {{ font-size: .92rem; font-weight: 600; text-align: right;
                   padding: .18rem .3rem; color: {INK}; }}

  /* 사이드바 지표별 기준선 표 */
  table.legend-tbl {{ margin-top: .55rem; }}
  table.legend-tbl th {{ font-size: .64rem; }}
  table.legend-tbl tbody th {{ font-weight: 500; }}
  table.legend-tbl td {{ font-size: .74rem; font-weight: 600; }}
  table.legend-tbl td.dim {{ color: #A5A199; font-weight: 500; }}
  table.legend-tbl tr.cur th, table.legend-tbl tr.cur td {{ color: {INK}; }}
  table.legend-tbl tr.cur {{ background: #F0EEE9; }}

  .note {{ font-size: .69rem; color: #949087; line-height: 1.45; margin-top: .4rem; }}

  .legend {{ font-size: .72rem; color: #7A766E; line-height: 1.9;
             font-variant-numeric: tabular-nums; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px;
             margin-right: .35rem; vertical-align: middle; }}

  .stButton button {{ background: #FFFFFF; border: 1px solid #DDD9D2;
                      color: {INK}; font-size: .78rem; border-radius: 6px;
                      width: 100%; }}
  details summary {{ font-size: .8rem !important; color: #6E6A62 !important; }}
</style>
"""


# ─────────────────────────────────────────────────────────────
# 사이드바 — 조작·범례·기준 표기를 본문에서 걷어낸다
# ─────────────────────────────────────────────────────────────

def candidate_picker(sb) -> None:
    """사이드바 「후보 목록」 — 244곳을 정렬해 훑고 고른다.

    **지도에서 찾아 들어가는 경로만으로는 전체를 훑을 수 없다.** "개선 큰 곳부터
    보고 싶다"에 답할 자리가 여기다. 지도 클릭은 위치를 알 때, 목록은 모를 때 쓴다.

    정렬 축을 둘 다 두는 이유 — 개선율과 총절감은 순위 상관 +0.32 로 다른 것을
    잰다(상위 20 중 4곳만 겹친다). 하나만 두면 그 축이 정답인 것처럼 읽힌다.
    """
    g = load_placement()
    sb.markdown('<div class="map-title">후보 목록</div>', unsafe_allow_html=True)
    if g.empty:
        sb.markdown(note_html(
            "시뮬 결과 파일이 없습니다 — <code>python src/evaluate.py</code> 를 "
            "돌리면 목록이 채워집니다."), unsafe_allow_html=True)
        return

    sb.radio("정렬", ["rate", "total"],
             format_func=lambda k: "개선율 순" if k == "rate" else "총절감 순",
             key="cand_sort", horizontal=True, label_visibility="collapsed")

    opts = candidate_options(st.session_state.cand_sort)
    ids = [c for c, _ in opts]
    label_of = dict(opts)
    cur = st.session_state.get("sim_cand")
    idx = ids.index(cur) if cur in ids else 0

    sb.selectbox("후보", ids, index=idx, key="cand_pick",
                 format_func=lambda c: label_of[c], label_visibility="collapsed")
    if sb.button("이 후보 결과 보기 →"):
        st.session_state.sim_cand = st.session_state.cand_pick
        st.session_state.page = 3
        st.rerun()

    n_weak = int((~g["sample_ok"]).sum())
    sb.markdown(note_html(
        f"{len(g)}곳 · 개선율과 총절감을 함께 적었습니다 — 두 축은 다른 것을 "
        f"잽니다(순위 상관 +0.32). <b>순위 번호는 붙이지 않습니다</b>: 대비 구간이 "
        f"겹치는 후보끼리는 우열을 가릴 수 없습니다.<br>"
        f"※표본부족 <b>{n_weak}곳</b> — 3km 콜 {E.MIN_CALLS_SCOPE:,}건 미만이라 "
        f"개선율이 커 보여도 닿는 사람이 적습니다."), unsafe_allow_html=True)


def sidebar(poly: pd.DataFrame) -> None:
    sb = st.sidebar
    sb.markdown('<div class="map-title">색칠 지표</div>', unsafe_allow_html=True)
    # key= 로 session_state 가 단일 출처. value= 와 대입을 섞으면 한 박자
    # 늦게 반영된다.
    sb.radio("색칠 지표", list(METRICS),
             format_func=lambda k: METRICS[k]["label"],
             key="metric_key", label_visibility="collapsed")

    sb.divider()
    sb.markdown('<div class="map-title">레이어</div>', unsafe_allow_html=True)
    sb.toggle("차고지 (현행 44개)", key="show_depots")
    cand = load_candidates()
    sb.toggle(f"후보 주차장 (배정된 {len(cand)}곳)", key="show_cands")
    sb.markdown(
        f'<div class="legend">'
        f'<span class="swatch" style="background:{DEPOT_COLOR}"></span>현행 차고지<br>'
        f'<span class="swatch" style="background:{CAND_COLOR}"></span>후보 주차장'
        f'</div>', unsafe_allow_html=True)
    n_outdoor = cand.attrs.get("n_outdoor", len(cand))
    assigned_note = (
        f"옥외 후보 {n_outdoor}곳 중 <b>동에 배정된 {len(cand)}곳</b>만 찍습니다 — "
        "나머지는 어느 동의 거점도 되지 않아 시뮬에 들어가지 않습니다. "
        "점을 클릭하면 어느 동에 배정됐는지 나옵니다(한 곳이 여러 동을 맡기도 합니다). "
        if cand.attrs.get("assigned_only") else
        "<b>배정 결과 파일이 없어 옥외 후보 전체</b>를 찍습니다 — "
        "<code>python src/candidates.py</code> 를 돌리면 배정된 곳만 남습니다. ")
    sb.markdown(note_html(
        assigned_note +
        f"후보 풀 {cand.attrs['n_pool']}곳 중 옥외만 쓰는 이유는, "
        f"옥내·혼합 {cand.attrs['n_excluded_indoor_mixed']}곳이 주차장 진입 "
        "유효고를 확인할 소스가 없어 리프트 특장차가 들어갈 수 있는지 "
        "판정할 수 없기 때문입니다(가정 A-15). 목록은 보존돼 있어 전고를 확인하면 "
        "그대로 되살릴 수 있습니다."), unsafe_allow_html=True)

    sb.divider()
    key = st.session_state.metric_key
    spec = METRICS[key]
    summ = metric_summary()
    fold_v, n_red = summ[key]
    scale = spec.get("scale", 1)
    unit, digits = spec["unit"], spec["digits"]
    side = "미만" if spec["worse"] == "high" else "초과"
    beyond = "이상" if spec["worse"] == "high" else "이하"
    vals = poly[key].dropna() * scale
    worst = vals.max() if spec["worse"] == "high" else vals.min()
    fold_s = fmt(fold_v, unit, digits)

    sb.markdown('<div class="map-title">범례</div>', unsafe_allow_html=True)
    sb.markdown(
        f'<div class="legend">'
        f'<span class="swatch" style="background:{GREEN}"></span>{fold_s} {side}<br>'
        f'<span class="swatch" style="background:{RED_LIGHT}"></span>'
        f'{fold_s} {beyond} — <b>{n_red}개 동</b><br>'
        f'<span class="swatch" style="background:{RED_DARK}"></span>'
        f'{fmt(worst, unit, digits)} (최대)<br>'
        f'<span class="swatch" style="background:{NO_MATCH}"></span>지표 매칭 없음'
        f'</div>', unsafe_allow_html=True)

    # 지표별 기준선·빨강 수 — 지표를 바꾸기 전에 규모를 가늠할 수 있게
    cells = []
    for k, sp in METRICS.items():
        f_v, n = summ[k]
        basis = "절대" if isinstance(sp["fold"], (int, float)) else str(sp["fold"])
        mark = ' class="cur"' if k == key else ""
        cells.append(
            f'<tr{mark}><th>{sp["label"]}</th>'
            f'<td>{fmt(f_v, sp["unit"], sp["digits"])}</td>'
            f'<td class="dim">{basis}</td><td>{n}</td></tr>')
    sb.markdown(
        '<table class="grid legend-tbl"><thead><tr><th></th><th>기준선</th>'
        f'<th>기준</th><th>빨강</th></tr></thead><tbody>{"".join(cells)}</tbody>'
        '</table>', unsafe_allow_html=True)

    if isinstance(spec["fold"], (int, float)):
        sb.markdown(note_html(
            f"{fold_s} 미만은 한 색으로 접었습니다. 기관 기준이 아니라 급한 곳만 "
            "눈에 남기기 위한 장치입니다. 시뮬 before/after 를 같은 자로 읽으려면 "
            "절대값이어야 합니다."), unsafe_allow_html=True)
    else:
        pos = "상위" if spec["worse"] == "high" else "하위"
        sb.markdown(note_html(
            f"근거 있는 절대 임계가 없어 <b>{pos} 25%</b>({spec['fold']})를 "
            "기준선으로 씁니다."), unsafe_allow_html=True)

    sb.divider()
    candidate_picker(sb)

    sb.divider()
    n_poly, n_col = len(poly), int(poly["has_metrics"].sum())
    sb.markdown(note_html(
        f"데이터 기준 <b>{BASIS}</b><br>"
        f"경계 {n_poly}개 중 {n_col}개 표시 · 미매칭 {n_poly - n_col}개<br>"
        "이용률은 방향(높을수록 좋음/나쁨)을 자료로 가릴 수 없어 "
        "색칠 지표에서 뺐습니다. 패널에는 표시됩니다."), unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# 페이지 — pages/ 분리가 아니라 session_state 조건부 렌더링.
# 선택 상태를 들고 다녀야 한다(명세 7절).
# ─────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    ss = st.session_state
    ss.setdefault("page", 1)
    ss.setdefault("selected", None)
    ss.setdefault("show_depots", True)
    ss.setdefault("show_cands", True)
    ss.setdefault("metric_key", DEFAULT_METRIC)
    ss.setdefault("sim_cand", None)          # 3페이지가 보는 후보 cand_id
    ss.setdefault("cand_sort", "rate")

    df = load_metrics()
    poly = resolve_polygons()
    summ = seoul_summary()

    if ss.page == 3:
        render_sim_page()
        return

    sidebar(poly)

    st.markdown(f'<div class="app-title">{APP_TITLE}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="app-sub">{BASIS} 기준 · 실무 진단용</div>',
                unsafe_allow_html=True)

    # 서울 전체 요약 — 개별 동 값을 읽을 때의 비교 기준
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("서울 평균 대기", f"{summ['mean_wait']:.1f}분")
    m2.metric(f"{RED_THRESHOLD_MIN:.0f}분 이상 동", f"{summ['n_red']}개")
    m3.metric("지역 지니", f"{summ['gini']:.3f}")
    m4.metric("표시 동", f"{summ['n_colored']}개")

    left, right = st.columns([4, 1], gap="medium")

    with left:
        spec = METRICS[ss.metric_key]
        st.markdown(f'<div class="map-title">{MAP_TITLE}</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="map-sub">{spec["label"]}으로 색칠 · 동을 클릭하면 '
            f'세부지표가 열립니다</div>', unsafe_allow_html=True)

        fig = build_map(poly, ss.selected, ss.show_depots, ss.show_cands,
                        ss.metric_key)
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun",
                             selection_mode="points", key="map")

        pts = []
        if ev is not None:
            sel = ev.get("selection") if isinstance(ev, dict) else getattr(ev, "selection", None)
            pts = (sel or {}).get("points", []) if sel else []
        for p in pts:
            cd = p.get("customdata")
            # 후보 점 — 동 선택과 섞이면 안 되므로 태그로 먼저 가른다.
            # 후보를 누르면 **3페이지(시뮬 결과)** 로 간다. 예전에는 오른쪽에
            # 작은 제원 패널만 띄웠는데, 그 내용은 3페이지 「후보 제원」으로
            # 옮겼다 — 목록으로 들어와도 같은 것을 볼 수 있어야 한다.
            if isinstance(cd, (list, tuple)) and len(cd) > 7 and cd[0] == CAND_TAG:
                cid = int(cd[7])
                if cid != ss.sim_cand or ss.page != 3:
                    ss.sim_cand = cid
                    ss.page = 3
                    st.rerun()
                continue
            loc = p.get("location")
            if loc and loc != ss.selected:
                ss.selected = loc
                ss.page = 2
                st.rerun()

    with right:
        if ss.selected is None:
            st.markdown(
                '<div class="panel">' + note_html(
                    "동을 클릭하면 세부지표가 여기 나타납니다.<br><br>"
                    "핵심 5개는 항상 보이고 나머지는 접혀 있습니다.")
                + '</div>', unsafe_allow_html=True)
        else:
            # 전체보기는 패널 맨 위 — 동 이름 바로 위
            if st.button("← 전체 보기 (선택 해제)"):
                ss.selected = None
                ss.page = 1
                st.rerun()

            row = poly[poly["adm_cd2"] == ss.selected]
            st.markdown('<div class="panel">', unsafe_allow_html=True)
            if len(row) and row.iloc[0]["has_metrics"]:
                dong_panel(row.iloc[0], df)
            else:
                nm = row.iloc[0]["adm_nm"] if len(row) else "?"
                st.markdown(f'<div class="dong-name">{nm}</div>', unsafe_allow_html=True)
                st.markdown(
                    '<div class="warn">이 경계에 대응하는 지표 행이 없습니다. '
                    '경계 파일(2023)과 콜 원본의 동 구분이 달라 생긴 공백입니다.</div>',
                    unsafe_allow_html=True)
            orphans = poly.attrs.get("orphan_rows", [])
            if orphans:
                names = " · ".join(o["dong"] for o in orphans)
                st.markdown(note_html(
                    f"지도에 실리지 않는 지표 {len(orphans)}개: {names}. "
                    "대응 경계가 없어 색칠 대상에서 빠집니다."), unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)


def sim_help_panel(row) -> None:
    """해석 도움 — **이 화면에서 하지 말아야 할 읽기**를 먼저 막는다.

    순서에 뜻이 있다. 겹침 → 표본 → 절대크기 → 기준. 앞의 둘은 이 후보에만
    해당하는 경고이고 뒤의 둘은 어느 후보에나 붙는 상시 주석이다.
    """
    n_ov = int(row["n_overlap"])
    if n_ov:
        st.markdown(
            f'<div class="warn"><b>우열을 가릴 수 없는 후보 {n_ov}곳</b><br>'
            f'대비 구간({row["interval"]})이 이 {n_ov}곳과 겹칩니다. '
            f'그 안에서는 어느 쪽이 낫다고 말할 수 없습니다 — '
            f'{"겹침이 이만큼 넓다는 것 자체가 결과입니다." if n_ov >= 100 else "선택은 다른 근거로 하십시오."}'
            f'</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="warn"><b>구간이 겹치는 후보가 없습니다</b><br>'
            '이 후보는 다른 243곳과 대비 구간이 떨어져 있습니다 — '
            '드문 경우입니다.</div>', unsafe_allow_html=True)

    if not bool(row["sample_ok"]):
        st.markdown(
            f'<div class="warn"><b>표본 부족</b><br>3km 콜 '
            f'{int(row["n_calls_scope"]):,}건으로 수혜 범위가 좁습니다'
            f'(기준 {E.MIN_CALLS_SCOPE:,}건). 개선율이 커 보여도 닿는 사람이 '
            f'그만큼 적습니다 — 총절감({row["total_delta_min"]:,.0f}분)을 함께 '
            f'보십시오. 계산에서 뺀 것은 아닙니다.</div>', unsafe_allow_html=True)

    st.markdown(note_html(
        f"<b>절감폭의 절대 크기는 과소평가입니다.</b> 시뮬 픽업 수준이 실측의 "
        f"{PICKUP_FIDELITY_PCT}%라(A-19·A-20) 여기 나온 분·%도 그만큼 축소돼 "
        f"있습니다. <b>후보 간 비교로 읽으십시오</b> — 모든 후보가 같은 편의를 "
        f"겪으므로 상대 비교는 유효합니다."), unsafe_allow_html=True)
    st.markdown(note_html(
        f"<b>{SIM_BASIS}</b> 기준({SIM_DAYS}일). 진단 화면의 <b>{BASIS}</b> 값과 "
        f"다릅니다 — 두 화면의 숫자를 그대로 견주지 마십시오."),
        unsafe_allow_html=True)


def sim_dong_panel(cand_id: int, row) -> None:
    """담당 동 목록 + 영향권 규모.

    **한 후보가 여러 동을 맡는다(최대 5개).** 어느 동들인지 적지 않으면
    A동에서 클릭한 후보와 B동에서 클릭한 후보가 같을 때 혼란이 생긴다.
    """
    dongs = [d for d in str(row["dongs"]).split(" · ") if d]
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown(f'<div class="dong-name">담당 동 {len(dongs)}개</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="dong-sub">이 후보를 거점으로 배정받은 동</div>',
                unsafe_allow_html=True)
    st.markdown(rows_html([(f"{i}", d) for i, d in enumerate(dongs, 1)]),
                unsafe_allow_html=True)

    sc = placement_scope().get(cand_id, {})
    st.markdown(rows_html([
        ("3km 영향권", f"{int(row['n_dong_scope'])}개 동"),
        ("영향권 콜", f"{int(row['n_calls_scope']):,}건"),
    ]), unsafe_allow_html=True)
    st.markdown(note_html(
        "<b>담당 동과 영향권은 다릅니다.</b> 담당은 이 후보를 거점으로 배정받은 "
        "동이고, 영향권은 거점에서 3km 안에 드는 동 전부입니다 — 개선율은 "
        "영향권 전체의 콜 가중 평균입니다."), unsafe_allow_html=True)

    # 지도에 그려지는 경계 수가 동 수보다 적을 수 있다 — 침묵하면 지도가 거짓말이 된다
    n_un = int(sc.get("n_unmapped", 0))
    if n_un:
        st.markdown(note_html(
            f"지도에는 <b>{len(sc['polys'])}개 경계</b>로 그려집니다. "
            f"{n_un}개 동은 경계 파일(2023)과 콜 원본의 동 구분이 달라 "
            f"합쳐졌거나 대응 경계가 없습니다 — 개선율 계산에는 "
            f"{int(row['n_dong_scope'])}개 동이 다 들어가 있습니다."),
            unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_sim_page() -> None:
    ss = st.session_state
    st.markdown(f'<div class="app-title">{APP_TITLE}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="app-sub">배치안 시뮬레이션 · {SIM_BASIS}</div>',
                unsafe_allow_html=True)

    candidate_picker(st.sidebar)
    st.sidebar.divider()
    if st.sidebar.button("← 지도로 돌아가기"):
        ss.page = 2 if ss.selected else 1
        st.rerun()

    row = placement_row(ss.sim_cand) if ss.sim_cand is not None else None
    if row is None:
        st.markdown(
            '<div class="panel"><div class="warn"><b>결과가 없습니다</b><br>'
            '이 후보의 시뮬 결과가 <code>outputs/placement_grades.csv</code> 에 '
            '없습니다. <code>python src/evaluate.py</code> 로 244곳을 돌린 뒤 다시 '
            '보십시오.</div></div>', unsafe_allow_html=True)
        if st.button("← 지도로"):
            ss.page = 2 if ss.selected else 1
            st.rerun()
        return

    # ── 후보 정보
    st.markdown(f'<div class="dong-name">{row["cand_name"]} ({row["gu"]})</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="dong-sub">콜 {int(row["n_calls_scope"]):,}건 · '
        f'동 {int(row["n_dong_scope"])}개 · '
        f'총절감 {row["total_delta_min"]:,.0f}분</div>', unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("픽업 개선율", f"{row['rate_pct']:+.2f}%",
              help="3km 영향권 콜 가중 평균. 값이 음수면 대기가 줄었다는 뜻입니다.")
    m2.metric("대비 구간", f"± {row['half']:.2f}%p",
              help="평균 ± 1.41σ. 신뢰구간이 아니라 두 후보를 가를 수 있는지 "
                   "보는 폭입니다.")
    m3.metric("총 절감", f"{row['total_delta_min']:,.0f}분",
              help="영향권 안에서 사라진 총 대기 분 = 분/콜 × 콜수.")
    m4.metric("픽업 before → after",
              f"{row['before']:.2f} → {row['after']:.2f}분")

    left, right = st.columns([4, 1], gap="medium")

    with left:
        sc = placement_scope().get(int(row["cand_id"]), {})
        n_poly, n_un = len(sc.get("polys", [])), int(sc.get("n_unmapped", 0))
        drawn = (f'{int(row["n_dong_scope"])}개 동' if not n_un
                 else f'{int(row["n_dong_scope"])}개 동(경계 {n_poly}개)')
        st.markdown('<div class="map-title">3km 영향권</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="map-sub">영향권 {drawn}을 한 색으로 칠했습니다 · '
            f'진한 테두리가 담당 동 · 주황 점이 후보 위치</div>',
            unsafe_allow_html=True)
        st.plotly_chart(build_sim_map(int(row["cand_id"]), row),
                        width="stretch", key=f"simmap-{int(row['cand_id'])}")
        st.markdown(note_html(
            "<b>영향권 안이 한 색인 것은 데이터의 한계입니다.</b> before/after 는 "
            "3km 범위 전체를 콜 가중 평균한 값 하나이고 동별 값은 산출돼 있지 "
            "않습니다. 농도를 동마다 다르게 주면 없는 차이를 지어내는 것이 됩니다 — "
            "이 지도가 답하는 것은 <b>어디까지 닿는가</b>입니다. 농도는 후보끼리 "
            "비교하십시오(짙을수록 개선율이 큽니다)."), unsafe_allow_html=True)

    with right:
        if st.button("← 지도로"):
            ss.page = 2 if ss.selected else 1
            st.rerun()

        sim_dong_panel(int(row["cand_id"]), row)

        st.markdown('<div class="panel">', unsafe_allow_html=True)
        st.markdown('<div class="dong-name">해석 도움</div>', unsafe_allow_html=True)
        sim_help_panel(row)
        st.markdown('</div>', unsafe_allow_html=True)

        with st.expander("후보 제원"):
            cand_spec_rows(int(row["cand_id"]))

        with st.expander("시드 3회 원값"):
            ev = load_placement_seeds()
            sub = ev[ev["cand_id"] == row["cand_id"]].sort_values("seed")
            if len(sub):
                cells = "".join(
                    f'<tr><th>{int(s.seed)}</th><td>{s.before:.2f}</td>'
                    f'<td>{s.after:.2f}</td><td>{s.rate_pct:+.2f}%</td></tr>'
                    for s in sub.itertuples())
                st.markdown(
                    '<table class="grid"><thead><tr><th>시드</th><th>before</th>'
                    f'<th>after</th><th>개선율</th></tr></thead><tbody>{cells}'
                    '</tbody></table>'
                    + note_html(
                        f"시드 간 σ {row['rate_sd']:.3f}%p. 같은 배치안이라도 "
                        f"배차 순서가 갈리면 이후 궤적이 전부 갈립니다 — 이 "
                        f"흔들림이 대비 구간의 근거입니다."),
                    unsafe_allow_html=True)
            else:
                st.markdown(note_html("원값 파일이 없습니다."),
                            unsafe_allow_html=True)


if __name__ == "__main__":
    main()
