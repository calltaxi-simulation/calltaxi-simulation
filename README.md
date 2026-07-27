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

데이터는 저장소에 포함되지 않음(대용량). `oracle/data/` 에 위치.
아래 파일이 필요하며, 코드는 상위 `../data/` 를 기본 경로로 참조한다.

| 파일 | 용도 |
|---|---|
| 서울시설공단_장애인콜택시 탑승내역_20251231.csv | 콜 원본(시뮬 입력) |
| calltaxi_2025_병합.csv | 차량번호 병합본 |
| 차고지44_좌표.csv | 현행 거점 44개 위치 |
| 행정동_중심점.csv | 동 중심 좌표 |
| HangJeongDong_ver20230701.geojson | 동 경계 |
| 동별_거점용량_접근성.csv | 과소공급 동(3km내 10대 이하) |
| 공영주차장_목록.csv / 시영주차장_목록.csv | 후보지 원본 |

## 실행

```bash
# 데이터 로딩 점검 (원본 → 필터·좌표매칭 결과 요약)
python src/load.py

# 시뮬 실행 (현행 배치 재현 → 관문 C 검증)
python src/simulator.py

# 대시보드
streamlit run src/dashboard.py

# 테스트 (관문 B)
pytest
pytest -m "not slow"    # 대용량 CSV를 읽는 테스트 제외
```

## 구조

```
simulation/
├ requirements.txt  # 실행 환경(venv + pip)
├ src/
│  ├ load.py         # 데이터 로딩·전처리
│  ├ travel_time.py  # 이동시간 테이블(실측 OD + 거리 보조)
│  ├ simulator.py    # 시뮬 엔진(SimPy)
│  ├ metrics.py      # 성과지표(대기·지니·장기대기·공차)
│  └ dashboard.py    # Streamlit
├ docs/
│  └ calibration.md  # 필터 정의·타깃 근거·데이터 한계
├ tests/            # pytest
├ cache/            # 콜 원본 parquet 캐시(자동 생성, git 제외)
└ outputs/          # 결과
```

## 검증 기준 (관문 C 캘리브레이션 타깃)

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
