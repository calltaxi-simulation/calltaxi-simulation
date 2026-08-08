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

import load  # noqa: E402
import metrics as M  # noqa: E402

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
    out.attrs["n_outdoor"] = n_outdoor
    out.attrs["assigned_only"] = True
    return out.reset_index(drop=True)


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
        cdata = [[CAND_TAG, n, int(c), s, g, -1 if pd.isna(a) else int(a), d]
                 for n, c, s, g, a, d in zip(
                     cand["name"], cand["capacity"], cand["source"], cand["gu"],
                     cand["capacity_available"], cand["assigned_dongs"])]
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


def cand_panel(cd: list) -> None:
    """클릭한 후보 주차장.

    cd 는 지도 customdata —
    [태그, 이름, 면수, 소스, 자치구, 실가용면수(없으면 -1), 배정 동].

    **면수가 시뮬 입력이고 실가용은 참고값이다.** 실측 잔여면은 시영 65곳에만
    있어서 그것을 용량으로 쓰면 후보 간 비교에서 자가 섞인다. 시영일 때만
    한 줄 더 붙여 "총 면수만 보면 안 된다"를 화면에서 알 수 있게 한다.

    **배정 동을 함께 보인다.** 후보 하나가 여러 동의 거점이 될 수 있고(대체 배정),
    그 경우 이 한 자리에 배치하면 여러 동이 같이 움직인다 — 클릭했을 때 그
    사실이 보여야 후보의 무게를 읽을 수 있다.
    """
    _, name, capacity, source, gu, available, dongs = cd
    rows = [("면수", f"{int(capacity):,}면"), ("소스", source), ("자치구", gu)]
    if dongs:
        n = dongs.count("·") + 1
        rows.append((f"배정 동 ({n})", dongs))
    note = "옥외 후보입니다. 시뮬 입력 용량은 이 총 면수입니다."
    if available >= 0:
        rows.insert(1, ("실가용 면수 (참고)", f"{int(available):,}면"))
        note = ("옥외 후보입니다. <b>시뮬 입력은 총 면수</b>이고, 실가용 면수는 "
                "정보공개청구 <b>피크시간 잔여구획</b>의 일별 중앙값 — 그 날 가장 "
                "붐빈 1시간에 실제로 비어 있던 면입니다. 시영 65곳에만 있어 "
                "용량으로는 쓰지 않고, 시뮬 뒤 후보를 좁힐 때 참고합니다. "
                "하루 중 최악값이라 야간 여력은 이보다 큽니다.")
    st.markdown(
        '<div class="panel">'
        f'<div class="dong-name">{name}</div>'
        + rows_html(rows) + note_html(note)
        + '</div>', unsafe_allow_html=True)


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
    ss.setdefault("selected_cand", None)
    ss.setdefault("metric_key", DEFAULT_METRIC)

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
            # 후보 점 — 동 선택과 섞이면 안 되므로 태그로 먼저 가른다
            if isinstance(cd, (list, tuple)) and cd and cd[0] == CAND_TAG:
                if list(cd) != ss.selected_cand:
                    ss.selected_cand = list(cd)
                    st.rerun()
                continue
            loc = p.get("location")
            if loc and loc != ss.selected:
                ss.selected = loc
                ss.page = 2
                st.rerun()

    with right:
        if ss.selected_cand is not None:
            cand_panel(ss.selected_cand)
            if st.button("후보 정보 닫기"):
                ss.selected_cand = None
                st.rerun()

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


def render_sim_page() -> None:
    st.markdown(f'<div class="app-title">{APP_TITLE}</div>', unsafe_allow_html=True)
    st.markdown('<div class="app-sub">배치안 시뮬레이션 · 구현 예정</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="panel">'
        '<div class="warn"><b>구현 예정</b><br>거점 배치안을 넣고 대기 개선폭을 '
        '보는 화면입니다. 시뮬 엔진(<code>simulator.py</code>)이 아직 미구현입니다.</div>'
        + note_html(
            "이 화면만 <b>대표 기간</b> 기준이 됩니다. 나머지 화면은 2025년 연간 "
            "기준입니다. 대표 기간은 연간 평균 대기(40.8분)와 가장 가까운 구간으로 "
            "고르며, 결과를 보기 전에 고정합니다(가정 A-03).")
        + '</div>', unsafe_allow_html=True)
    if st.button("← 지도로"):
        st.session_state.page = 2 if st.session_state.selected else 1
        st.rerun()


if __name__ == "__main__":
    main()
