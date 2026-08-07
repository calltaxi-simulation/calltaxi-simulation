"""
load.py — 데이터 로딩·전처리

원천 CSV/GeoJSON을 읽어 시뮬레이터가 쓸 형태로 정리한다.

데이터는 대용량이라 저장소에 포함하지 않는다. 기본 경로는 저장소 상위의 ../data/ 이고,
폴더 구조가 다르면 환경변수 DATA_DIR 로 덮어쓴다. 필요한 파일 목록은 README 참조.

    export DATA_DIR=/path/to/data     # macOS/Linux
    $env:DATA_DIR = "D:\\calltaxi"    # Windows PowerShell

콜 원본은 338MB·173만 행이라 매번 파싱하면 느리다. 필터·파생까지 마친
결과를 cache/ 에 parquet 으로 떨궈두고, 원본이 바뀌면 자동으로 다시 만든다.

반환 스키마는 snake_case 영문으로 통일한다. simulator/metrics/travel_time 의
시그니처가 이미 영문(origin, dest, hour, mean_wait)이라 그쪽에 맞췄다.
원천 한글 컬럼과의 대응은 각 함수 docstring 참조.
"""
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# 저장소 상위의 data/ — clone 위치가 어디든 이 파일 기준으로 잡힌다.
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def resolve_data_dir() -> Path:
    """데이터 폴더 결정. 환경변수 DATA_DIR 이 있으면 그쪽, 없으면 ../data/.

    팀원마다 clone 구조가 달라도 경로만 지정하면 돌아가게 하려는 것이다.
    import 시점에 한 번 읽어 DATA_DIR 에 담는다 — 실행 중에 환경변수를 바꿨다면
    이 함수를 다시 불러 받아야 한다.
    """
    env = os.environ.get("DATA_DIR", "").strip()
    return Path(env).expanduser().resolve() if env else DEFAULT_DATA_DIR


DATA_DIR = resolve_data_dir()

# 캐시는 산출물이라 데이터 폴더가 아니라 저장소 안에 둔다(읽기전용 데이터 폴더 대응).
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

# 서울 25개 자치구. 원본에 경기·인천 건이 섞여 있어 화이트리스트로 거른다.
SEOUL_GU = (
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구",
    "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구",
    "강동구",
)

# 즉시콜 판정: |예정 − 접수| 이하면 즉시콜
IMMEDIATE_TOLERANCE_MIN = 2

# 대기시간 상한(분). 초과분은 기록 오류로 보고 제외 — 실측 p99가 127분이라 여유를 뒀다.
MAX_WAIT_MIN = 360

# 2023 경계 기준으로 통폐합된 동.
# step3_match.py(저장소 밖 탐색 스크립트, docs/external_sources.md 5절)와 동일한 판정.
MANUAL_DONG = {
    ("종로구", "명륜3가동"): "혜화동",
    ("동대문구", "용두동"): "용신동",   # 용신동 = 용두동 + 신설동
    ("동대문구", "신설동"): "용신동",
}

# 시간대 구분 — travel_time 의 OD 테이블 키와 공유한다.
PERIOD_BINS = [0, 6, 10, 17, 22, 24]
PERIOD_LABELS = ["심야", "아침", "낮", "저녁", "심야"]


# ─────────────────────────────────────────────────────────────
# 동 이름 정규화
# ─────────────────────────────────────────────────────────────

def canon_dong(name: str) -> str:
    """동 이름 표준형. '고덕제1동' → '고덕1동'.

    콜 원본은 '제N동', 경계 GeoJSON 은 'N동' 표기를 써서 그냥 두면
    441개 중 219개가 어긋난다. 공백·가운뎃점·마침표를 지우고
    숫자 앞의 '제'를 반복 제거한다(성수2가제3동 같은 중첩 대응).
    """
    s = str(name).replace(" ", "").replace("·", "").replace(".", "")
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"제(?=\d)", "", s)
    return s


def _base_dong(canon: str) -> str:
    """숫자와 꼬리 '동'을 뗀 기본명. '화곡1동' → '화곡'. 분동/합동 대응용."""
    b = re.sub(r"\d", "", canon)
    return b[:-1] if b.endswith("동") else b


def build_dong_lookup(path: Path = None) -> pd.DataFrame:
    """(구, 동) → 중심좌표 조회표.

    3단계로 맞춘다:
      exact   표준형이 그대로 일치 (콜 원본 441개 조합 중 413)
      manual  행정구역 개편으로 이름이 바뀐 동 (3)
      approx  분동/합동이라 1:N 이면 같은 기본명 그룹의 중심점 평균 (16)
    맞지 않는 나머지는 표에 넣지 않는다(인천 중구 동이 서울 중구로 섞여 들어온 건 등).

    exact/manual 은 표준형(key_kind='canon')으로, approx 는 숫자를 뗀
    기본명(key_kind='base')으로 색인한다. 조회는 canon 먼저, 실패 시 base 순.

    반환 컬럼: gu, key, key_kind, adm_nm, lat, lon, match_type
    """
    path = Path(path) if path else DATA_DIR / "행정동_중심점.csv"
    cen = pd.read_csv(path, encoding="utf-8-sig")

    parts = cen["adm_nm"].str.split(" ", expand=True)
    cen["gu"] = parts[1]
    # '서울특별시 종로구 사직동' → '사직동'. 동명에 공백이 있으면 붙여준다.
    cen["key"] = parts.iloc[:, 2:].fillna("").agg("".join, axis=1).map(canon_dong)

    exact = cen[["gu", "key", "adm_nm", "lat", "lon"]].copy()
    exact["key_kind"] = "canon"
    exact["match_type"] = "exact"

    manual = []
    for (gu, src), target in MANUAL_DONG.items():
        hit = exact[(exact["gu"] == gu) & (exact["key"] == canon_dong(target))]
        if not hit.empty:
            r = hit.iloc[0]
            manual.append({"gu": gu, "key": canon_dong(src), "adm_nm": r["adm_nm"],
                           "lat": r["lat"], "lon": r["lon"],
                           "key_kind": "canon", "match_type": "manual"})
    manual = pd.DataFrame(manual, columns=exact.columns)

    # 기본명 그룹 평균 — 원본의 '상일동'이 경계상 '상일1동+상일2동'으로 갈린 경우
    grp = (cen.assign(base=cen["key"].map(_base_dong))
              .groupby(["gu", "base"])
              .agg(adm_nm=("adm_nm", lambda s: "+".join(x.split()[-1] for x in s)),
                   lat=("lat", "mean"), lon=("lon", "mean"))
              .reset_index()
              .rename(columns={"base": "key"}))
    grp["key_kind"] = "base"
    grp["match_type"] = "approx"

    cols = ["gu", "key", "key_kind", "adm_nm", "lat", "lon", "match_type"]
    out = pd.concat([exact[cols], manual[cols], grp[cols]], ignore_index=True)
    return out.drop_duplicates(subset=["gu", "key", "key_kind"], keep="first")


def time_period(hour) -> pd.Categorical:
    """시각(0~23) → 아침/낮/저녁/심야. travel_time 의 시간대 키와 동일 기준."""
    return pd.cut(pd.Series(hour), bins=PERIOD_BINS, labels=PERIOD_LABELS,
                  right=False, ordered=False)


# ─────────────────────────────────────────────────────────────
# 콜 원본
# ─────────────────────────────────────────────────────────────

_TIME_COLS = {
    "접수일시": "received_at", "예정일시": "scheduled_at", "배차일시": "assigned_at",
    "승차일시": "boarded_at", "하차일시": "alighted_at", "취소일시": "canceled_at",
}
_PLAIN_COLS = {
    "출발구": "origin_gu", "출발동": "origin_dong", "목적구": "dest_gu", "목적동": "dest_dong",
    "이용목적": "purpose", "요금": "fare", "승차거리": "ride_distance_m",
    "차량구분": "vehicle_type", "장애유형": "disability_type",
}


def load_calls(path: Path = None, *, require_seoul_dest: bool = False,
               use_cache: bool = True, chunksize: int = 500_000) -> pd.DataFrame:
    """콜 탑승내역 로딩.

    - 즉시콜 필터(|예정−접수| <= 2분)
    - 서울 25구 필터(출발 기준. require_seoul_dest=True 면 목적지도 서울로 한정)
    - 대기시간 = 승차 − 접수 계산
    - 출발동·목적동에 중심좌표를 붙인다(좌표를 못 찾는 출발동은 제외)

    원본 173만 행 중 즉시콜 88.3%, 서울 출발이 그 대부분이다.
    결과는 cache/calls_*.parquet 에 저장하고 원본 mtime·크기가 같으면 재사용한다.

    반환 컬럼(원천 한글 → 영문):
      접수/예정/배차/승차/하차/취소일시 → received_at ... canceled_at
      출발구·출발동 → origin_gu, origin_dong  (+ _canon, _lat, _lon)
      목적구·목적동 → dest_gu, dest_dong      (+ _canon, _lat, _lon)
      요금·승차거리·차량구분·장애유형·이용목적 → fare, ride_distance_m, ...
      파생: wait_min(승차−접수), assign_min(배차−접수), ride_min(하차−승차),
            is_canceled, date, hour, weekday, period
    """
    path = Path(path) if path else DATA_DIR / "서울시설공단_장애인콜택시 탑승내역_20251231.csv"
    cache = _cache_path(path, require_seoul_dest)

    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    lookup = build_dong_lookup()
    usecols = list(_TIME_COLS) + list(_PLAIN_COLS)
    kept = []
    n_raw = 0

    reader = pd.read_csv(path, encoding="utf-8-sig", usecols=usecols,
                         chunksize=chunksize, low_memory=False)
    for chunk in reader:
        n_raw += len(chunk)
        kept.append(_prepare_chunk(chunk, lookup, require_seoul_dest))

    calls = pd.concat(kept, ignore_index=True)
    calls.insert(0, "call_id", np.arange(len(calls), dtype=np.int32))
    calls = _shrink(calls)
    calls.attrs["n_raw"] = n_raw

    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        calls.to_parquet(cache, index=False)
    return calls


def _prepare_chunk(chunk: pd.DataFrame, lookup: pd.DataFrame,
                   require_seoul_dest: bool) -> pd.DataFrame:
    """청크 하나에 필터·파생을 적용. 메모리 때문에 나눠 처리한다."""
    df = chunk.rename(columns={**_TIME_COLS, **_PLAIN_COLS})
    for col in _TIME_COLS.values():
        df[col] = pd.to_datetime(df[col], errors="coerce")

    # 즉시콜
    gap = (df["scheduled_at"] - df["received_at"]).abs().dt.total_seconds() / 60
    df = df[gap <= IMMEDIATE_TOLERANCE_MIN]

    # 서울
    df = df[df["origin_gu"].isin(SEOUL_GU)]
    if require_seoul_dest:
        df = df[df["dest_gu"].isin(SEOUL_GU)]
    if df.empty:
        return df

    df["origin_dong_canon"] = df["origin_dong"].map(canon_dong)
    df["dest_dong_canon"] = df["dest_dong"].map(canon_dong)

    # 좌표 부착
    df = _attach_coords(df, lookup, "origin")
    df = _attach_coords(df, lookup, "dest")
    # 출발 좌표는 배차 계산에 필수 — 못 찾으면 버린다(인천 중구 등 오적재 건)
    df = df[df["origin_lat"].notna()]

    # 파생
    df["wait_min"] = (df["boarded_at"] - df["received_at"]).dt.total_seconds() / 60
    df["assign_min"] = (df["assigned_at"] - df["received_at"]).dt.total_seconds() / 60
    df["ride_min"] = (df["alighted_at"] - df["boarded_at"]).dt.total_seconds() / 60
    df["is_canceled"] = df["boarded_at"].isna()

    # 음수·비현실 대기는 기록 오류로 보고 결측 처리(행은 남긴다 — 취소율 분모 유지)
    bad = (df["wait_min"] < 0) | (df["wait_min"] > MAX_WAIT_MIN)
    df.loc[bad, "wait_min"] = np.nan

    df["date"] = df["received_at"].dt.normalize()
    df["hour"] = df["received_at"].dt.hour.astype("int8")
    df["weekday"] = df["received_at"].dt.weekday.astype("int8")
    df["period"] = time_period(df["hour"].to_numpy()).astype(str)

    df["dest_in_seoul"] = df["dest_gu"].isin(SEOUL_GU)
    return df.reset_index(drop=True)


def _attach_coords(df: pd.DataFrame, lookup: pd.DataFrame, side: str) -> pd.DataFrame:
    """출발/목적 동에 중심좌표를 붙인다. side 는 'origin' 또는 'dest'.

    표준형으로 먼저 맞추고, 남은 건만 기본명으로 다시 맞춘다.
    (분동/합동 동은 표준형이 경계 파일에 없어서 이 2단계가 필요하다)
    """
    gu, canon = f"{side}_gu", f"{side}_dong_canon"
    lat, lon, match = f"{side}_lat", f"{side}_lon", f"{side}_match"

    def _right(kind, key_col):
        return (lookup[lookup["key_kind"] == kind]
                .rename(columns={"gu": gu, "key": key_col,
                                 "lat": lat, "lon": lon, "match_type": match})
                [[gu, key_col, lat, lon, match]])

    df = df.merge(_right("canon", canon), on=[gu, canon], how="left")

    unresolved = df[lat].isna()
    if unresolved.any():
        base_col = f"{side}_dong_base"
        df[base_col] = df[canon].map(_base_dong)
        fb = df.loc[unresolved, [gu, base_col]].merge(
            _right("base", base_col), on=[gu, base_col], how="left")
        for col in (lat, lon, match):
            df.loc[unresolved, col] = fb[col].to_numpy()
        df = df.drop(columns=base_col)

    return df


# 저카디널리티 문자열 — object 로 두면 150만 행에서 1.4GB 를 먹는다
_CATEGORICAL = (
    "origin_gu", "origin_dong", "origin_dong_canon", "origin_match",
    "dest_gu", "dest_dong", "dest_dong_canon", "dest_match",
    "purpose", "vehicle_type", "disability_type", "period",
)
_FLOAT32 = ("fare", "ride_distance_m", "wait_min", "assign_min", "ride_min",
            "origin_lat", "origin_lon", "dest_lat", "dest_lon")


def _shrink(df: pd.DataFrame) -> pd.DataFrame:
    """dtype 축소. 청크별로 하면 카테고리가 어긋나므로 합친 뒤 한 번에 한다."""
    for col in _CATEGORICAL:
        df[col] = df[col].astype("category")
    for col in _FLOAT32:
        df[col] = df[col].astype("float32")
    return df


def _cache_path(src: Path, require_seoul_dest: bool) -> Path:
    """원본 mtime·크기를 파일명에 박아 원본이 바뀌면 캐시가 자동으로 빗나가게 한다."""
    st = src.stat()
    tag = "od" if require_seoul_dest else "o"
    return CACHE_DIR / f"calls_{tag}_{int(st.st_mtime)}_{st.st_size}.parquet"


# ─────────────────────────────────────────────────────────────
# 거점·경계·후보지
# ─────────────────────────────────────────────────────────────

def load_depots(path: Path = None) -> pd.DataFrame:
    """현행 차고지 44개 좌표·수용대수 로딩.

    반환 컬럼: depot_id, name, gu, address, lat, lon, capacity, operator, is_paid
    (차량대수 합 691대)
    """
    path = Path(path) if path else DATA_DIR / "차고지44_좌표.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")
    out = df.rename(columns={
        "연번": "depot_id", "차고지명": "name", "소속구": "gu", "상세주소": "address",
        "차량대수": "capacity", "차고지_관리주체": "operator",
    })[["depot_id", "name", "gu", "address", "lat", "lon", "capacity", "operator"]]
    out["is_paid"] = df["차고지_유무료"].eq("유료")
    out["capacity"] = out["capacity"].astype(int)
    return out


def load_dong(path: Path = None, *, with_boundary: bool = False, crs: int = 4326):
    """행정동 중심점·경계 로딩.

    with_boundary=False (기본) → 중심점 DataFrame (gu, dong_canon, adm_nm, lat, lon)
    with_boundary=True         → 경계 GeoDataFrame (geopandas 필요, 34MB GeoJSON)

    원본 GeoJSON 은 EPSG:4326 이다. 거리 계산을 미터로 하려면 crs=5179 를 넘겨
    투영좌표로 받는다(README 의 EPSG:5179 기준).
    """
    if not with_boundary:
        cen = build_dong_lookup(path)
        cen = cen[cen["match_type"] == "exact"].rename(columns={"key": "dong_canon"})
        return cen[["gu", "dong_canon", "adm_nm", "lat", "lon"]].reset_index(drop=True)

    import geopandas as gpd  # 무거워서 경계가 필요할 때만 부른다

    path = Path(path) if path else DATA_DIR / "HangJeongDong_ver20230701.geojson"
    gdf = gpd.read_file(path, engine="pyogrio", where="adm_nm LIKE '서울특별시%'")
    gdf["gu"] = gdf["adm_nm"].str.split(" ").str[1]
    gdf["dong_canon"] = gdf["adm_nm"].str.split(" ").str[2].map(canon_dong)
    if crs and gdf.crs and gdf.crs.to_epsg() != crs:
        gdf = gdf.to_crs(crs)
    return gdf[["adm_nm", "adm_cd2", "gu", "dong_canon", "geometry"]]


_POOL_COLS = {
    "주차장명": "name", "주소": "address", "자치구": "gu",
    "법정동": "bdong", "법정동코드": "bdong_cd",
    "면수": "capacity", "위도": "lat", "경도": "lon",
    "소스": "source", "공공근거": "public_basis",
    "UPIS대분류": "upis_lclas", "UPIS시설명": "upis_name", "고시번호": "notice_no",
    "옥내외": "enclosure", "옥내자주": "indoor_self", "옥외자주": "outdoor_self",
    "현행차고지": "current_depot", "현행차고지거리m": "current_depot_dist_m",
    "운영시간": "open_hours", "급지": "price_grade", "이용효율": "usage_efficiency",
}

# 정보공개청구 17078071 — 파일명에 접수번호가 박혀 있어 이것으로 찾는다.
# data/ 아래 어느 깊이에 두어도 걸리게 재귀 탐색한다(압축 해제 폴더가 한 겹 더 있다).
FOIA_PARKING_GLOB = "**/*17078071*.xlsx"
PEAK_RESIDUAL_SHEET = "2025 피크시간 잔여"


def find_foia_parking_xlsx(data_dir: Path = None) -> Path | None:
    """정보공개청구(17078071) 원본 xlsx 경로. 없으면 None."""
    root = Path(data_dir) if data_dir else DATA_DIR
    # '~$...' 는 엑셀이 열어둘 때 만드는 잠금 파일이라 걸리면 안 된다
    hits = sorted(p for p in root.glob(FOIA_PARKING_GLOB)
                  if not p.name.startswith("~$"))
    return hits[0] if hits else None


def load_peak_residual(path: Path = None) -> pd.DataFrame:
    """시영주차장 피크시간 잔여구획 → 주차장별 실가용 면수.

    정보공개청구 17078071 의 `2025 피크시간 잔여` 시트(40,515행 = 111곳 × 365일).
    한 행이 "그 날 가장 붐빈 1시간에 몇 면이 비어 있었나"다.

    **총 면수가 아니라 이 값이 거점 평가의 분모다.** 362면짜리 복정역도 피크에
    남는 면은 중앙 23면이고, 방화역(동) 67면은 365일 내내 0면이다. 총 면수로
    후보를 고르면 상시 만차인 주차장을 대형 후보로 착각한다.

    대표값은 **일별 피크잔여의 중앙값**을 쓴다. 평균은 명절·연휴의 큰 값에
    끌려간다 — 개화산역은 중앙 21면인데 평균 47.7면(최대 212)이다.

    ⚠ 한계 두 가지.
      - 피크시간은 그 날 가장 붐빈 때라 **하루 중 최악값**이다. 차고지는 박차
        (17시~익일 07시, A-09)가 주 용도라 야간 여력은 이 값보다 크다.
        보수적 하한으로 읽어야 한다.
      - 명칭 조인이라 이름이 바뀌면 조용히 빠진다. 조인 결과는 호출부에서
        건수로 확인한다.

    반환 컬럼: name, capacity_available, avail_mean, avail_p25, avail_max, n_days
    """
    path = Path(path) if path else find_foia_parking_xlsx()
    if path is None:
        raise FileNotFoundError(
            f"정보공개청구 17078071 xlsx 를 {DATA_DIR} 아래에서 찾지 못했다")

    df = pd.read_excel(path, sheet_name=PEAK_RESIDUAL_SHEET)
    df["name"] = df["주차장명"].astype(str).str.strip()

    g = df.groupby("name")["잔여구획"]
    out = pd.DataFrame({
        "capacity_available": g.median(),
        "avail_mean": g.mean(),
        "avail_p25": g.quantile(0.25),
        "avail_max": g.max(),
        "n_days": g.size(),
    }).reset_index()
    out.attrs["source_path"] = str(path)
    return out


def load_candidates(path: Path = None, *, outdoor_only: bool = True,
                    with_peak_residual: bool = True) -> pd.DataFrame:
    """시뮬 거점 후보 풀 로딩 — `sim_pool_v4.csv` (639곳 · 55,798면).

    공영·시영 원본 목록을 직접 읽던 방식을 대체한다. v4 풀은 공영·시영에 더해
    KOTSA 주차장을 UPIS 도시계획시설 폴리곤과 공간조인해 공공성을 판정하고,
    건축물대장으로 민간·기계식을 걸러낸 결과다. 만들어진 과정은
    docs/sim-pool.pipeline.v4.md 참조.

    **기본은 옥외 348곳 24,392면만 돌려준다(A-15).** 리프트 특장차(전고 2.6m)가
    들어갈 수 있어야 거점이 되는데, 주차장 진입 유효고를 주는 소스가 없어
    옥내 163곳·혼합 128곳은 진입 가능 여부를 판정할 수 없다(파이프라인 8-1,
    미해결 #2). 기계식은 풀 단계에서 이미 빠졌지만 자주식 옥내라도 유효고가
    낮으면 리프트 차량은 못 들어간다. 혼합도 뺀다 — 파이프라인이 혼합 111곳은
    사실상 옥내이고 "옥외분만 골라 쓴다"는 계산은 신뢰할 수 없다고 못박았다.

    **제외분은 버리지 않는다.** `outdoor_only=False` 로 부르면 639곳 전체가
    나온다. 현장에서 유효고를 확인하면 그대로 재편입할 후보다.

    **시뮬 입력 용량은 `capacity` = 총 면수 하나로 통일한다.** 실측 잔여면은
    시영 65곳에만 있어서, 그것을 용량으로 쓰면 65곳은 잔여면 · 283곳은 총 면수가
    되어 **후보 간 비교에서 자가 섞인다.** 배치 효과의 후보별 차이가 위치가 아니라
    측정 기준의 차이에서 나오게 된다.

    실가용 면수는 버리지 않고 **후보 품질 참고값**으로 남긴다.
      `capacity_available`        시영 65곳 — 피크시간 잔여구획의 일별 중앙값
      `capacity_available_ratio`  실가용 / 총 면수
    시영 총 6,950면의 실가용은 1,097면(15.8%)이다. 시뮬 결과가 나온 뒤 후보를
    좁힐 때 쓴다 — 190면짜리라도 피크에 상시 만차면 거점으로 쓸 수 없다.
    나머지 283곳에는 잔여 실측 자체가 없어 NaN 이다(0 이 아니다).

    cand_id 는 원본 CSV 의 행 순서(1부터)다. 풀을 다시 만들면 번호가 바뀐다 —
    저장 키가 아니라 실행 안에서의 참조용이다.

    반환 컬럼: cand_id, name, address, gu, bdong, bdong_cd, lat, lon,
              capacity(시뮬 입력), capacity_available, capacity_available_ratio,
              source, public_basis, upis_lclas, upis_name, notice_no,
              enclosure, is_outdoor, indoor_self, outdoor_self,
              current_depot, current_depot_dist_m,
              open_hours, price_grade, usage_efficiency
    """
    path = Path(path) if path else DATA_DIR / "sim_pool_v4.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")

    missing = set(_POOL_COLS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} 에 없는 컬럼: {sorted(missing)}")

    out = df.rename(columns=_POOL_COLS)[list(_POOL_COLS.values())].copy()
    out.insert(0, "cand_id", np.arange(1, len(out) + 1, dtype=np.int32))
    out["capacity"] = out["capacity"].round().astype(int)
    out["is_outdoor"] = out["enclosure"].eq("옥외")

    n_all = len(out)
    n_by_enclosure = out["enclosure"].value_counts().to_dict()
    cap_by_enclosure = out.groupby("enclosure")["capacity"].sum().to_dict()

    if not out["gu"].isin(SEOUL_GU).all():
        bad = sorted(set(out["gu"]) - set(SEOUL_GU))
        raise ValueError(f"서울 25구가 아닌 자치구가 섞여 있다: {bad}")

    # 실가용 면수 — 참고 컬럼이다. 시뮬 입력 capacity 에는 손대지 않는다.
    out["capacity_available"] = np.nan
    n_available = 0
    avail_note = "미부착"
    if with_peak_residual:
        try:
            avail = load_peak_residual()
        except (FileNotFoundError, ImportError, ValueError) as e:
            avail_note = f"미부착 ({type(e).__name__}: {e})"
        else:
            m = out["name"].str.strip().map(
                avail.set_index("name")["capacity_available"])
            # 잔여 실측은 시영 소스에만 있다. 이름이 같은 공영·KOTSA 건에
            # 잘못 붙지 않도록 소스로 한 번 더 막는다.
            m = m.where(out["source"].eq("시영"))
            out["capacity_available"] = m
            n_available = int(m.notna().sum())
            avail_note = f"시영 {n_available}곳"

    out["capacity_available_ratio"] = out["capacity_available"] / out["capacity"]

    if outdoor_only:
        out = out[out["is_outdoor"]]
    out = out.reset_index(drop=True)

    out.attrs["n_pool"] = n_all
    out.attrs["n_by_enclosure"] = n_by_enclosure
    out.attrs["capacity_by_enclosure"] = cap_by_enclosure
    out.attrs["n_excluded_indoor_mixed"] = (n_by_enclosure.get("옥내", 0)
                                            + n_by_enclosure.get("혼합", 0))
    out.attrs["n_capacity_available"] = n_available
    out.attrs["capacity_available_note"] = avail_note

    cols = ["cand_id", "name", "address", "gu", "bdong", "bdong_cd", "lat", "lon",
            "capacity", "capacity_available", "capacity_available_ratio",
            "source", "public_basis", "upis_lclas", "upis_name", "notice_no",
            "enclosure", "is_outdoor", "indoor_self", "outdoor_self",
            "current_depot", "current_depot_dist_m",
            "open_hours", "price_grade", "usage_efficiency"]
    return out[cols]


def load_disabled_population(path: Path = None) -> pd.DataFrame:
    """동별 등록 장애인 수(장애유형 합계 · 성별 계).

    통계표를 그대로 받은 파일이라 정형이 아니다. 세 가지를 흡수한다:
      - 행 끝에 쉼표가 남아 빈 컬럼이 하나 더 붙고 열 수가 어긋난 행이 섞여 있다
        → on_bad_lines='skip' + engine='python'
      - 구 컬럼이 따로 없고 '동별' 한 컬럼에 총계·구·동·'기타'가 섞여 있다.
        구 행이 그 구의 동들 앞에 오는 순서라 ffill 로 구를 채운다.
      - 값이 '-' 인 칸이 있다(집계 없음) → NaN

    '기타'(구마다 1행)는 동 단위가 없는 잔여분이라 동 지표에 못 쓴다. 총계·구 행과
    함께 뺀다. 남는 427개 동의 합은 384,921명으로 표 전체 합계(384,934)와 13명 차이다.

    반환 컬럼: gu, dong, dong_canon, dong_base, n_disabled
    """
    path = Path(path) if path else DATA_DIR / "서울시_장애인_통계_2025.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", on_bad_lines="skip", engine="python")

    df = df[(df["장애유형별"] == "합계") & (df["성별"] == "계")].copy()
    # 구 행이 동 행보다 먼저 나오는 순서에 기댄다 — 원본 정렬이 바뀌면 여기가 깨진다.
    df["gu"] = df["동별"].where(df["동별"].isin(SEOUL_GU)).ffill()

    # 연도 컬럼명이 갱신마다 바뀐다('2025 년' → '2026 년'). 마지막 연도 컬럼을 쓴다.
    year_cols = [c for c in df.columns if re.fullmatch(r"\d{4}\s*년", str(c))]
    if not year_cols:
        raise ValueError(f"연도 컬럼을 찾지 못했다: {list(df.columns)}")

    out = df[~df["동별"].isin(SEOUL_GU) & ~df["동별"].isin(("합계", "기타"))].copy()
    out = out.rename(columns={"동별": "dong"})
    out["n_disabled"] = pd.to_numeric(out[year_cols[-1]], errors="coerce")
    out["dong_canon"] = out["dong"].map(canon_dong)
    out["dong_base"] = out["dong_canon"].map(_base_dong)

    cols = ["gu", "dong", "dong_canon", "dong_base", "n_disabled"]
    return out[cols].reset_index(drop=True)


_VEHICLE_COLS = ["차량번호", "승차일시", "하차일시", "승차거리"]


def load_vehicle_trips(path: Path = None) -> pd.DataFrame:
    """차량별 운행 기록. 조 편성 실측 재구성용(A-09 — metrics.vehicle_day_table).

    travel_time.load_rides 와 같은 원본이지만 목적이 달라 필터가 다르다. 저쪽은
    동A→동B 이동시간 표를 만드는 게 목적이라 출발·목적이 모두 서울인 건만 쓰지만,
    여기서는 서울 밖 운행도 차량이 묶여 있던 시간이라 구를 가리지 않는다.

    필터: 승차·하차 시각이 모두 기록된 완료 건, 운행시간 1~120분, 차량번호 있음.
    차량번호가 비어 있는 건(원본 병합이 차량을 못 붙인 건)은 차량-일 집계의 키가
    없어 뺀다. 제외 건수는 attrs['n_no_vehicle'] 에 남긴다 — groupby 가 조용히
    떨어뜨리게 두지 않으려는 것이다.

    차량번호는 877개다. 차고지 정원 합 691대보다 많은데, 1년치라 중간에
    교체·증차된 차량이 모두 잡히기 때문이다. 특정 시점의 가동 대수가 아니다.

    service_date 는 승차 시각 기준이라 자정을 넘긴 운행은 다음 날로 넘어간다.
    심야 비중이 1.7%라 하루 단위 집계에는 영향이 거의 없다.

    반환 컬럼: vehicle_id, service_date, boarded_at, alighted_at, ride_min, km
    """
    path = Path(path) if path else DATA_DIR / "calltaxi_2025_merged.csv"
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=_VEHICLE_COLS, low_memory=False)
    n_raw = len(df)

    board = pd.to_datetime(df["승차일시"], format="ISO8601", errors="coerce")
    alight = pd.to_datetime(df["하차일시"], format="ISO8601", errors="coerce")
    ride_min = (alight - board).dt.total_seconds() / 60

    ok = board.notna() & alight.notna() & ride_min.between(1.0, 120.0)
    df, board, alight, ride_min = df[ok], board[ok], alight[ok], ride_min[ok]

    vehicle = pd.to_numeric(df["차량번호"], errors="coerce")
    has_vehicle = vehicle.notna()
    n_no_vehicle = int((~has_vehicle).sum())
    df, board, alight, ride_min, vehicle = (
        df[has_vehicle], board[has_vehicle], alight[has_vehicle],
        ride_min[has_vehicle], vehicle[has_vehicle])

    out = pd.DataFrame({
        "vehicle_id": vehicle.astype("int64").to_numpy(),
        "service_date": board.dt.normalize().to_numpy(),
        "boarded_at": board.to_numpy(),
        "alighted_at": alight.to_numpy(),
        "ride_min": ride_min.astype("float32").to_numpy(),
        "km": (pd.to_numeric(df["승차거리"], errors="coerce") / 1000)
              .astype("float32").to_numpy(),
    })
    out.attrs["n_raw"] = n_raw
    out.attrs["n_no_vehicle"] = n_no_vehicle
    return out.reset_index(drop=True)


def load_undersupplied(path: Path = None, *, max_vehicles_3km: int = 10) -> pd.DataFrame:
    """과소공급 동(3km내 차량 10대 이하) 로딩.

    동별_거점용량_접근성.csv 는 분석 단계(step4_distance.py — 저장소 밖 탐색
    스크립트, docs/external_sources.md 5절)의 산출물로,
    동별 실측 대기와 반경별 차량대수를 이미 붙여둔 표다. 기본 임계 10대에서 68개 동.

    반환 컬럼: gu, dong, dong_canon, mean_wait, long_wait_ratio, n_calls,
              nearest_depot, nearest_km, vehicles_3km, supply_level, lat, lon
    """
    path = Path(path) if path else DATA_DIR / "동별_거점용량_접근성.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")

    out = df.rename(columns={
        "출발구": "gu", "출발동": "dong", "평균대기": "mean_wait",
        "장기대기율": "long_wait_ratio", "건수": "n_calls",
        "최근접차고지명": "nearest_depot", "최근접거리km": "nearest_km",
        "3km내차량대수": "vehicles_3km", "공급수준": "supply_level",
    })
    out["dong_canon"] = out["dong"].map(canon_dong)
    out["long_wait_ratio"] = out["long_wait_ratio"] / 100   # 원본이 퍼센트 표기

    cols = ["gu", "dong", "dong_canon", "mean_wait", "long_wait_ratio", "n_calls",
            "nearest_depot", "nearest_km", "vehicles_3km", "supply_level", "lat", "lon"]
    out = out[out["vehicles_3km"] <= max_vehicles_3km]
    return out[cols].sort_values("vehicles_3km").reset_index(drop=True)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    src = "환경변수 DATA_DIR" if os.environ.get("DATA_DIR", "").strip() else "기본값 ../data/"
    print(f"DATA_DIR: {DATA_DIR}  ({src})")
    print("존재:", DATA_DIR.exists())
    if not DATA_DIR.exists():
        print("→ 폴더 구조가 다르면 환경변수 DATA_DIR 로 지정한다. 파일 목록은 README 참조.")
        raise SystemExit(1)
    print()

    lookup = build_dong_lookup()
    print(f"동 조회표      {len(lookup):>10,}개  "
          f"{lookup['match_type'].value_counts().to_dict()}")

    depots = load_depots()
    print(f"차고지         {len(depots):>10,}개  차량 {depots['capacity'].sum()}대")

    cands = load_candidates()
    enc, cap = cands.attrs["n_by_enclosure"], cands.attrs["capacity_by_enclosure"]
    print(f"후보 풀        {cands.attrs['n_pool']:>10,}곳  "
          + " / ".join(f"{k} {enc[k]}곳 {cap[k]:,.0f}면" for k in ("옥외", "옥내", "혼합")))
    print(f"후보(옥외만)   {len(cands):>10,}곳  {cands['capacity'].sum():,}면"
          f"  — 옥내·혼합 {cands.attrs['n_excluded_indoor_mixed']}곳 제외(A-15)")
    sy = cands["capacity_available"].notna()
    print(f"  실가용 면수  {cands.attrs['capacity_available_note']}"
          f"  · 시영 옥외 {int(cands.loc[sy, 'capacity'].sum()):,}면 →"
          f" {int(cands.loc[sy, 'capacity_available'].sum()):,}면"
          f"  (시뮬 입력은 총 면수)")

    under = load_undersupplied()
    print(f"과소공급 동    {len(under):>10,}개")

    print("\n콜 로딩 중(첫 실행은 1~2분, 이후 캐시)...")
    calls = load_calls()
    n_raw = calls.attrs.get("n_raw")
    print(f"콜             {len(calls):>10,}건" + (f"  (원본 {n_raw:,}행)" if n_raw else "  (캐시)"))
    print(f"  기간         {calls['date'].min():%Y-%m-%d} ~ {calls['date'].max():%Y-%m-%d}")
    print(f"  취소율       {calls['is_canceled'].mean():.1%}")
    print(f"  목적지 서울  {calls['dest_in_seoul'].mean():.1%}")
    print(f"  출발동 매칭  {calls['origin_match'].value_counts().to_dict()}")
    n_dest_gap = int(calls["dest_lat"].isna().sum())
    print(f"  목적동 좌표없음 {n_dest_gap:,}건 ({n_dest_gap/len(calls):.1%}) "
          f"— 대부분 서울 밖, travel_time 에서 거리 보조로 처리")

    w = calls["wait_min"].dropna()
    print(f"\n대기시간(분)   평균 {w.mean():.1f} / 중앙값 {w.median():.1f} / "
          f"p90 {w.quantile(.9):.1f} / 60분초과 {(w > 60).mean():.1%}")
    print("검증 기준      평균 39.3 / 중앙값 30.8 / p90 77.2 / 60분초과 17.9%")
