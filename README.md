# 장애인콜택시 대기거점 분석

서울 장애인콜택시 운영을 실측 데이터로 진단하고, 대기거점 배치안 평가를 위한
이산사건 시뮬레이터(DES) 설계의 저장소다. 

## 환경 설정

Python 3.14.3 에서 검증됐다. 하위 버전은 시험하지 않았다. venv + pip 으로 구성한다(conda 아님).

```bash
# 1. 가상환경 생성 (최초 1회)
python -m venv .venv

# 2. 활성화 (작업할 때마다)
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# 3. 패키지 설치 (최초 1회)
pip install -r requirements.txt
```

**pandas 는 2.x 로 고정한다.** `requirements.txt` 가 `pandas>=2.0,<3.0` 으로 상한을 둔다.

pandas 3.0 은 **copy-on-write 를 기본으로 켠다.** 그러면 사본에 대한 연쇄 대입으로
동작하던 코드가 원본을 더 이상 바꾸지 않게 되고, `.loc` 대입·`assign` 후 view/copy
여부에 따라 **같은 코드에서 결과가 갈릴 수 있다.** 이 저장소는
`load._prepare_chunk()` 의 `df.loc[bad, "wait_min"] = np.nan`,
`metrics.dong_population()` 의 `out.loc[solo.index, ...]` 처럼 슬라이스에 직접
대입하는 자리가 여러 곳이라 영향을 받는다. 값이 어긋나면 검증 6개 대조가 조용히
통과하거나 조용히 틀릴 수 있어 2.x 안에서만 재현한다.

전역 파이썬이 아니라 `.venv` 를 쓰는 이유도 같다 — 전역 환경의 pandas 가 이 핀을
위반할 수 있다.

### 검증된 조합

실제로 설치돼 아래 산출값을 낸 버전이다.

| 항목 | 값 |
|---|---|
| OS | Windows 11, 빌드 **10.0.26200** (AMD64) |
| Python | **3.14.3** (`tags/v3.14.3:323c59a`, MSC v.1944 64bit) |

| 패키지 | 핀 | 설치 | 패키지 | 핀 | 설치 |
|---|---|---|---|---|---|
| pandas | `>=2.0,<3.0` | **2.3.3** | scipy | `>=1.11` | 1.18.0 |
| numpy | `>=1.24,<3.0` | **2.5.1** | streamlit | `>=1.30` | 1.60.0 |
| geopandas | `>=0.14` | **1.1.4** | matplotlib | `>=3.7` | 3.11.1 |
| shapely | `>=2.0` | 2.1.2 | pyarrow | `>=14.0` | 24.0.0 |
| pyogrio | `>=0.7` | 0.13.0 | pytest | `>=7.4` | 9.1.1 |
| pyproj | `>=3.6` | 3.7.2 | jupyter | — | 설치됨 |
| **simpy** | `>=4.0` | **4.1.2** | | | |

SimPy 는 DES 엔진이다. 이벤트 처리 방식·난수 생성기는
[docs/model_flow.md](docs/model_flow.md#엔진난수) 참조.

## 데이터 배치

데이터는 저장소에 포함되지 않음(대용량). 아래 파일을 저장소와 같은 상위 폴더의 data/ 에 둔다. 코드는 ../data/ 를 참조한다.

폴더 구조가 다르면 환경변수 `DATA_DIR` 로 덮어쓴다.

```bash
export DATA_DIR=/path/to/data     # macOS/Linux
$env:DATA_DIR = "D:\calltaxi"     # Windows PowerShell
```

| 파일 | 용도 |
|---|---|
| **calls_2025_replay.csv** | **콜 원본 — 특장차 한정본 139.1만 행.** 시뮬 입력 · 이동시간 · 유휴 구간 · 조 편성이 전부 이 파일 하나를 본다. 원본 명세에서 흡수한 사실은 [docs/calibration.md](docs/calibration.md) 첫 절 |
| 차고지44_좌표.csv | 현행 거점 44개 위치 |
| 행정동_중심점.csv | 동 중심 좌표 |
| 서울시_장애인_통계_2025.csv | 동별 등록 장애인(이용률 분모) |
| HangJeongDong_ver20230701.geojson | 동 경계 |
| 동별_거점용량_접근성.csv | 과소공급 동(3km내 10대 이하) |
| sim_pool_v4.csv | **거점 후보 풀 639곳 · 55,798면** — 옥외 348곳만 후보로 쓰고(가정 A-15), 그중 동에 배정된 244곳이 시뮬에 들어간다(candidates.py) |

`공영주차장_목록.csv` / `시영주차장_목록.csv` 는 더 이상 코드가 읽지 않는다 —
후보 풀 파이프라인의 입력으로 흡수됐고 저장소는 그 산출물(`sim_pool_v4.csv`)만 본다.

`서울시설공단_장애인콜택시 탑승내역_20251231.csv` 와 `calltaxi_2025_merged.csv` 도
더 이상 읽지 않는다 — **`calls_2025_replay.csv` 가 둘을 대체한다**(A 마스터에 차량번호를
붙이고 특장차만 남긴 정제본). 임차택시를 뺀 이유는 가정 A-16 을 볼 것: 차고지 기반
교대 운영이 아니라 거점 배치의 영향을 받지 않는다.

## 실행

```bash
# 데이터 로딩 점검 (원본 → 필터·좌표매칭 결과 요약)
python src/load.py

# 이동시간 테이블 점검 (커버율·샘플 조회·이상값)
python src/travel_time.py

# 동별 진단 지표 산출 (outputs/ 에 저장 → 전체값을 검증 기준과 대조)
python src/metrics.py

# 동별 거점 후보 배정 (내부 → 1km 대체 → 경계선상 → 보류)
python src/candidates.py

# 유휴 구간 산출 (하차 → 다음 배차)
python src/idle.py

# 인내심 분포 추정 (Kaplan-Meier — 시뮬 이탈 로직의 입력)
python src/patience.py

# 시뮬 실행 (현행 배치 재현 → 검증)
python src/simulator.py

# 후보 평가 (244곳 × 시드 3회 → 개선율·대비 구간·총절감)
python src/evaluate.py

# 가정 민감도 (A-01·A-17·A-18 — 대표 후보 9곳 × 설정 6개 → 순위 상관)
python src/sensitivity.py
python src/sensitivity.py --report    # 재생 없이 보고만

# 대시보드 (진단 화면 + 후보 결과)
streamlit run src/dashboard.py

# 테스트 (코드 점검)
pytest
pytest -m "not slow"    # 대용량 CSV를 읽는 테스트 제외
```

`evaluate.py` 는 중단·재개가 된다. 같은 명령을 다시 치면 이미 끝난 (후보, 시드)는
건너뛴다. 시드를 나눠 돌리려면 --seeds 42,43 처럼 지정한다.


## 구조

```
simulation/
├ requirements.txt  # 실행 환경(venv + pip)
├ src/
│  ├ load.py         # 데이터 로딩·전처리
│  ├ travel_time.py  # 이동시간 테이블(실측 OD + 거리 보조)
│  ├ metrics.py      # 동별 진단 지표(대기·차내·미이행·수요·공급·형평)
│  ├ idle.py         # 유휴 구간(하차 → 다음 배차)
│  ├ patience.py     # 인내심 분포(Kaplan-Meier)
│  ├ candidates.py   # 동별 거점 후보 배정(후보 풀 → 행정동 426개)
│  ├ simulator.py    # 시뮬 엔진(SimPy)
│  ├ evaluate.py     # 후보 평가 실행(244곳 × 시드 3회, 중단·재개)
│  ├ sensitivity.py  # 가정 민감도(A-01·A-17·A-18 — 대표 9곳 × 설정 6개)
│  └ dashboard.py    # Streamlit 대시보드(진단 + 후보 결과)
├ docs/
│  ├ assa_log.md         # 가정 대장(A-01~A-16) — 가정 부호의 정본
│  ├ calibration.md      # 정본 — 모듈별 필터·지표 정의·타깃 근거·한계
│  ├ provenance.md       # 산출값 대장(스크립트를 돌려 얻은 현재 값)
│  ├ model_flow.md       # 시뮬 설계 — 흐름도·조 편성·엔진·난수
│  ├ dashboard_spec.md   # 대시보드 화면 결정과 근거
│  └ external_sources.md # 코드 밖 근거 — 문헌·구술·소급 기록·저장소 밖 자료
├ tests/            # pytest
├ cache/            # 콜 원본 parquet 캐시(자동 생성, git 제외)
└ outputs/          # 결과
   ├ dong_metrics.csv/.parquet   # 동별 지표 표(432개 동) — 대시보드가 읽는다
   ├ dong_demand_matrix.parquet  # 동 × 시간대 × 평일/주말 콜 수
   ├ idle_gaps.csv               # 유휴 구간 요약 — src/idle.py 산출
   ├ idle_by_hour.csv            # 시간대별 평균 동시 유휴 차량 수
   ├ patience_km.csv             # 인내심 생존곡선(0.5분 격자) — src/patience.py 산출
   ├ patience_summary.csv        # 인내심 추정 요약 1행
   ├ dong_candidates.csv         # 동 426개 × 배정된 후보(383동 · 244곳) — src/candidates.py 산출
   ├ placement_eval.csv          # 후보 평가 원값(244곳 × 시드 3회 = 732행) — src/evaluate.py 산출
   ├ placement_grades.csv        # 후보별 개선율·대비 구간·총절감·겹침 수
   └ sensitivity_eval.csv        # 가정 민감도 원값(9곳 × 설정 6개 = 54행) — src/sensitivity.py 산출
```

지표 함수는 파일을 읽지 않고 데이터프레임만 받는다. 실측 콜(진단)과 시뮬 로그(예측)에
같은 함수를 그대로 통과시켜 before/after 를 비교하려는 것이다. 로딩은 load.py 담당.

## 검증 기준

현행 배치로 돌렸을 때 실측값 재현 여부로 판정한다.
**모집단은 특장차 한정이다(A-16).** 괄호는 임차택시를 포함하던 구 값이다.
**지표마다 분모가 다르니 대조할 때 주의할 것.**

| 지표 | 목표값 | 구 값 | 분모 |
|----|----|----|----|
| 평균 대기 | **40.8분** | 39.3 | 승차 완료 1,033,135 |
| 중앙값 | **32.0분** | 30.8 | 〃 |
| p90 | **80.4분** | 77.2 | 〃 |
| 장기대기(60분 초과) | **19.2%** | 17.9% | 〃 |
| 취소율 | **15.44%** | 13.3% | 시뮬 입력 모수 1,222,330 |
| 지역 지니(동 · 총 대기) | **0.092** | 0.0947 | 100건 이상 동 430개 |
| **대기 중 포기** † | **8.54%** | 6.84% | 시뮬 입력 모수 1,222,330 |

**대기가 오른 것은 특장차가 느려서가 아니라 다른 운영 체계가 빠졌기 때문이다.**
임차택시는 취소율 4.8% · 근무 7.94시간 · 첫 운행 10시 단일 봉우리로 특장차와 다르고,
차고지 기반 교대 운영이 아니라 거점 배치의 영향을 받지 않는다. 섞으면 어느 쪽도
재현되지 않는다(A-16).

**⚠ 시뮬 채점은 이 표가 아니다(2026.08.09).** 위는 **실측 진단**의 대장이고,
시뮬은 **픽업(승차−배차)·취소·픽업 지니**로 채점한다 — 거점 배치가 바꾸는 것은 픽업이고,
매칭(배차−접수)은 재현하지 못해 검증에서 뺐다(A-19). 총 대기 4개는 참고로 남긴다.
기준·근거는 [docs/calibration.md](docs/calibration.md) 의 「시뮬 채점 기준」 절.

| 판정 대상 | 대표기간 실측 | 연간 실측 | 허용 |
|---|---|---|---|
| 픽업 평균 / 중앙 / p90 | 18.80 / 17.76 / 29.62분 |18.78 / 17.81 / 29.40 | 0.5 / 0.5 / 1.0 |
| 취소율 | 15.56% | 15.44% | 0.002 |
| **픽업 지니** (`pickup_gini`) | 0.0751 | 0.0704 | 0.005 |

**대조는 대표 기간 값으로 한다**(`metrics.period_targets`). 시뮬이 대표 기간
(2025-08-20 ~ 09-18)을 재생하므로 같은 구간의 실측과 견줘야 한다. 지니는 기간
길이에 편향되어 연간과 대표월이 갈린다.

† **성격이 다르다.** 위 6개는 실측 진단의 집계값이고,
대기 중 포기는 인내심 곡선(A-14)이 배차 로직과 맞물려 재현해야 할 목표다. 곡선은
"얼마나 참을 수 있나"(성향)이고 8.54%는 "실제로 얼마나 포기했나"(결과)라, 배차가
느리면 포기가 늘고 빠르면 준다 — 둘이 서로를 검증한다.

**시뮬 차량 대수는 723이 아니라 평일 574 / 주말 374 를 요일별로 적용한다.**
723은 연간 누적 고유 차량번호라 그대로 넣으면 공급이 29% 과대해진다
(`metrics.daily_active_vehicles()`). 연간 중앙 561은 요일이 섞인 값이라 쓰지 않는다.

상세 근거·필터 정의·알려진 한계는 docs/calibration.md 참조.

## 재현성
난수는 NumPy PCG64. 후보 평가는 시드 42·43·44 세 회를 돌려 대비 구간을 낸다.
콜 ID 기반 스트림 분리(splitmix64)로 인내심·즉시취소·배차후취소를 가른다 —
배치안이 바뀌어도 같은 콜은 같은 값을 받는다.
라이브러리 버전은 requirements.txt 관리. 시드·설정을 바꾸면 기록한다.
결과는 outputs/ 에 저장하며, `placement_eval.csv` 는 (후보, 시드) 단위로
append 되어 중단·재개가 가능하다.
