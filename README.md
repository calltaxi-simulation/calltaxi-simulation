# 장애인콜택시 대기거점 시뮬레이터

서울 장애인콜택시 배치안을 평가하는 이산사건 시뮬레이터(DES).
실무자가 지정한 거점 배치안을 넣으면 대기시간·형평·비용 지표를 산출한다.

## 환경 설정

Python 3.11 이상. venv + pip 으로 구성한다(conda 아님).

```bash
# 1. 가상환경 생성 (최초 1회)
python -m venv .venv

# 2. 활성화 (작업할 때마다)
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# 3. 패키지 설치 (최초 1회)
pip install -r requirements.txt
```

**pandas 는 2.x 로 고정한다.** 3.0 은 copy-on-write 기본화 등 동작이 달라
같은 코드에서도 결과가 갈릴 수 있어 `requirements.txt` 에 상한을 뒀다.

검증된 조합: Python 3.14.3 / pandas 2.3.3 / numpy 2.5.1 / geopandas 1.1.4 / simpy 4.1.2

## 데이터 배치

데이터는 저장소에 포함되지 않음(대용량). 아래 파일을 저장소와 같은 상위 폴더의 data/ 에 둔다. 코드는 ../data/ 를 참조한다.

폴더 구조가 다르면 환경변수 `DATA_DIR` 로 덮어쓴다.

```bash
export DATA_DIR=/path/to/data     # macOS/Linux
$env:DATA_DIR = "D:\calltaxi"     # Windows PowerShell
```

| 파일 | 용도 |
|---|---|
| 서울시설공단_장애인콜택시 탑승내역_20251231.csv | 콜 원본(시뮬 입력) |
| calltaxi_2025_merged.csv | 차량번호 병합본 172.9만 행(이동시간·차량 생산성) |
| 차고지44_좌표.csv | 현행 거점 44개 위치 |
| 행정동_중심점.csv | 동 중심 좌표 |
| 서울시_장애인_통계_2025.csv | 동별 등록 장애인(이용률 분모) |
| HangJeongDong_ver20230701.geojson | 동 경계 |
| 동별_거점용량_접근성.csv | 과소공급 동(3km내 10대 이하) |
| 공영주차장_목록.csv / 시영주차장_목록.csv | 후보지 원본 |

## 실행

```bash
# 데이터 로딩 점검 (원본 → 필터·좌표매칭 결과 요약)
python src/load.py

# 이동시간 테이블 점검 (커버율·샘플 조회·이상값)
python src/travel_time.py

# 동별 진단 지표 산출 (outputs/ 에 저장 → 전체값을 검증 기준과 대조)
python src/metrics.py

# 시뮬 실행 (현행 배치 재현 → 검증)
python src/simulator.py

# 유휴 구간 산출 (하차 → 다음 배차)
python analysis/step7_idle.py

# 대시보드
streamlit run src/dashboard.py

# 테스트 (코드 점검)
pytest
pytest -m "not slow"    # 대용량 CSV를 읽는 테스트 제외
```

## 구조

```
simulation/
├ requirements.txt  # 실행 환경(venv + pip)
├ analysis/
│  └ step7_idle.py   # 유휴 구간 산출(하차 → 다음 배차)
├ src/
│  ├ load.py         # 데이터 로딩·전처리
│  ├ travel_time.py  # 이동시간 테이블(실측 OD + 거리 보조)
│  ├ simulator.py    # 시뮬 엔진(SimPy)
│  ├ metrics.py      # 동별 진단 지표(대기·차내·미이행·수요·공급·형평)
│  └ dashboard.py    # Streamlit
├ docs/
│  ├ calibration.md      # 필터 정의·지표 정의·타깃 근거·데이터 한계
│  ├ provenance.md       # 수치별 산출 근거(파일 → 전처리 → 정의)
│  ├ stress_checklist.md # STRESS-DES 보고 표준 충족 현황
│  ├ data_sources.md     # 데이터 출처(STRESS 3.1)
│  ├ preprocessing_log.md# 전처리 로그(STRESS 3.2)
│  ├ model_flow.md       # 모델 흐름도(STRESS 2.1)
│  └ tech_stack.md       # 기술 스택·난수(STRESS 5.1·5.2)
├ tests/            # pytest
├ cache/            # 콜 원본 parquet 캐시(자동 생성, git 제외)
└ outputs/          # 결과
   ├ dong_metrics.csv/.parquet   # 동별 지표 표(432개 동) — 대시보드가 읽는다
   ├ dong_demand_matrix.parquet  # 동 × 시간대 × 평일/주말 콜 수
   ├ vehicle_productivity.csv    # 차량 생산성 요약(동별 아님)
   ├ idle_gaps.csv               # 유휴 구간 요약 — analysis/step7_idle.py 산출
   └ idle_by_hour.csv            # 시간대별 평균 동시 유휴 차량 수
```

지표 함수는 파일을 읽지 않고 데이터프레임만 받는다. 실측 콜(진단)과 시뮬 로그(예측)에
같은 함수를 그대로 통과시켜 before/after 를 비교하려는 것이다. 로딩은 load.py 담당.

## 검증 기준

현행 배치로 돌렸을 때 실측값 재현 여부로 판정한다.
**지표마다 분모가 다르니 대조할 때 주의할 것.**

| 지표 | 목표값 | 분모 |
|---|---|---|
| 평균 대기 | 39.3분 | 승차 완료 1,323,620 |
| 중앙값 | 30.8분 | 〃 |
| p90 | 77.2분 | 〃 |
| 장기대기(60분 초과) | 17.9% | 〃 |
| 취소율 | 13.3% | 시뮬 입력 모수 1,527,213 |
| 지역 지니(동) | 0.095 | 100건 이상 동 430개 |

상세 근거·필터 정의·알려진 한계는 [docs/calibration.md](docs/calibration.md) 참조.

## 재현성

- 난수 시드 고정(코드에 명시). 시드 변경 시 기록.
- 라이브러리 버전은 requirements.txt 관리.
- 결과는 시드·설정과 함께 outputs/ 에 저장.
