# 기술 스택

**대응 STRESS 항목: 5.1 소프트웨어·프로그래밍 언어 / 5.2 난수 샘플링**

- **5.1** — 운영체제와 버전·빌드번호, DES 소프트웨어의 이름·버전·빌드번호, 범용
  프로그래밍 언어의 이름·버전, 사용한 프레임워크·라이브러리 전부를 버전 번호와 함께.
- **5.2** — 난수 표본 생성 알고리즘. 공통난수를 쓴다면 시드(또는 난수 스트림)를
  샘플링 프로세스들에 어떻게 배분하는지.

(이벤트 처리 메커니즘·우선순위 규칙은 5.3, 실행 시간·하드웨어 사양은 5.4 소관이라
이 문서에서는 DES 소프트웨어를 특정하는 데 필요한 만큼만 적는다.)

---

## 5.1 실행 환경

### 운영체제

| 항목 | 값 |
|---|---|
| OS | Windows 11 Home |
| 버전·빌드 | **10.0.26200** (`platform.platform()` → `Windows-11-10.0.26200-SP0`) |
| 아키텍처 | AMD64 (x86-64) |

### 언어·패키지 관리

| 항목 | 값 |
|---|---|
| 언어 | **Python 3.14.3** |
| 빌드 | `tags/v3.14.3:323c59a, Feb 3 2026, 16:04:56` / `MSC v.1944 64 bit (AMD64)` |
| 가상환경 | **venv + pip** — **conda 아님** |
| 위치 | `simulation/.venv` (전역 파이썬이 아니다) |
| 의존성 선언 | `simulation/requirements.txt` |

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
```

실행은 반드시 이 venv 로 한다.

```bash
.venv\Scripts\python.exe src/load.py
.venv\Scripts\python.exe src/travel_time.py
.venv\Scripts\python.exe src/metrics.py
```

### 검증된 조합

`requirements.txt` 의 핀은 범위이고, 아래는 **실제로 설치돼 결과를 낸 정확한 버전**이다.

| 패키지 | requirements.txt 핀 | 설치된 버전 | 역할 |
|---|---|---|---|
| pandas | `>=2.0,<3.0` | **2.3.3** | 전 데이터 처리·집계 |
| numpy | `>=1.24,<3.0` | **2.5.1** | 수치 연산·난수 |
| geopandas | `>=0.14` | **1.1.4** | 동 경계 GeoJSON |
| shapely | `>=2.0` | **2.1.2** | 지오메트리 |
| pyogrio | `>=0.7` | **0.13.0** | GeoJSON 읽기 엔진(geopandas 백엔드) |
| pyproj | `>=3.6` | **3.7.2** | 좌표계 변환 (EPSG:4326 ↔ 5179) |
| **simpy** | `>=4.0` | **4.1.2** | **DES 엔진** |
| scipy | `>=1.11` | **1.18.0** | 분포 적합·통계 |
| streamlit | `>=1.30` | **1.60.0** | 대시보드 |
| matplotlib | `>=3.7` | **3.11.1** | 시각화 |
| pyarrow | `>=14.0` | **24.0.0** | parquet 캐시 |
| pytest | `>=7.4` | **9.1.1** | 테스트 |
| jupyter | (핀 없음) | 설치됨 | 탐색·검증용 |

SimPy·NumPy·pandas 를 비롯한 위 패키지는 순수 Python/PyPI 배포판이라 별도의 빌드번호
체계가 없다 — 위 버전 문자열이 곧 빌드 식별자다.

### pandas 2.x 상한 근거

`requirements.txt` 가 `pandas>=2.0,<3.0` 으로 **상한을 둔다.**

pandas 3.0 은 **copy-on-write 를 기본으로 켠다.** 그러면 지금까지 사본에 대한 연쇄 대입
(chained assignment)으로 동작하던 코드가 원본을 더 이상 바꾸지 않게 되고, `.loc` 대입·
`assign` 후 view/copy 여부에 따라 **같은 코드에서 결과가 갈릴 수 있다.** 이 저장소는
`load._prepare_chunk()` 의 `df.loc[bad, "wait_min"] = np.nan`, `metrics.dong_population()`
의 `out.loc[solo.index, ...]` 처럼 슬라이스에 직접 대입하는 자리가 여러 곳이라 영향을 받는다.

값이 어긋나면 검증 6개 대조(`metrics.compare_to_target()`)가 조용히 통과하거나 조용히
틀릴 수 있으므로, 상한을 두고 **2.x 안에서만 재현**한다.

전역 파이썬이 아니라 `simulation/.venv` 를 쓰는 이유도 같다 — 전역 환경의 pandas 가
이 핀을 위반할 수 있다.

### DES 소프트웨어

| 항목 | 값 |
|---|---|
| 소프트웨어 | **SimPy 4.1.2** (오픈소스, Python 라이브러리) |
| 방식 | **process interaction** |
| 사용 위치 | `src/simulator.py` — `CallTaxiSim.__init__` 이 `simpy.Environment()` 를 만들고, 차량 한 대의 생애를 `vehicle_process()` 프로세스로 둔다 |

SimPy 는 파이썬 제너레이터를 프로세스로 삼아, 프로세스가 이벤트를 `yield` 하면 중단하고
스케줄러가 시간순 이벤트 큐에서 다음 이벤트를 꺼내 재개시키는 **프로세스 상호작용
(process interaction)** 방식이다. 이 모델에서는 차량 1대가 하나의 프로세스이고
`대기 → 배차 → 픽업 이동 → 탑승 → 운행 → 하차 → 대기` 를 한 제너레이터 안에서 진행한다.

> **구현 상태** — `src/simulator.py` 는 뼈대만 있다. `CallTaxiSim.__init__`, `dispatch`,
> `vehicle_process`, `run`, `run_placement` 이 모두 `raise NotImplementedError` 다.
> SimPy 환경 생성과 난수 생성기 초기화까지는 코드에 있으나, 프로세스 정의와 이벤트
> 스케줄링은 아직 없다. 자세한 대응은 [`model_flow.md`](model_flow.md) 참조.

### 저장 형식

| 형식 | 용도 |
|---|---|
| CSV (`utf-8-sig`) | 모든 원천 데이터. 인코딩은 읽는 쪽에서 `utf-8-sig` 로 고정 |
| Parquet (pyarrow) | `cache/` 의 콜·이동시간 캐시, `outputs/` 의 동별 지표·수요 매트릭스 |
| GeoJSON (EPSG:4326) | 동 경계. 미터 단위 거리가 필요하면 `load_dong(crs=5179)` 로 투영 |

---

## 5.2 난수 샘플링

### 생성 알고리즘

| 항목 | 값 |
|---|---|
| 생성기 | `numpy.random.default_rng(seed)` → `numpy.random.Generator` |
| 비트 생성기 | **PCG64** (Permuted Congruential Generator, 128비트 상태 / 64비트 출력) |
| **Mersenne Twister 아님** | MT19937 은 레거시 `numpy.random.RandomState` 의 기본이다. 이 저장소는 `default_rng` 를 쓰므로 **PCG64** 다 |
| 확인 방법 | `np.random.default_rng().bit_generator.state['bit_generator']` → `'PCG64'` |
| 사용 위치 | `simulator.CallTaxiSim.__init__` — `self.rng = np.random.default_rng(seed)` |
| 시드 | `simulator.SEED = 42` (모듈 상수) |

SimPy 자체는 난수를 만들지 않는다 — 이벤트 스케줄링만 한다. 확률 표본은 전부 NumPy
`Generator` 에서 나온다.

### 시드 배분·공통난수

**미정이다.**

현재 코드에는 `CallTaxiSim` 인스턴스마다 `default_rng(seed)` 로 생성기를 **하나** 만드는
줄만 있고, 그 아래는 `raise NotImplementedError` 다. 따라서 다음이 모두 정해지지 않았다.

| 항목 | 상태 |
|---|---|
| 샘플링 프로세스별 스트림 분리 | **미정.** 인내심 분포·이동시간 잔차·조 배정 등 확률 요소를 별도 스트림으로 가를지, 단일 생성기를 공유할지 결정되지 않았다 |
| **공통난수(CRN) 사용 여부** | **미정.** 배치안 A와 B를 비교할 때 같은 난수열을 쓸지 결정되지 않았다 |
| 배치안 간 시드 배분 규칙 | **미정** |
| 반복(replication) 수와 시드 목록 | **미정** (STRESS 4.3 소관) |

STRESS 5.2 는 "공통난수를 사용한다면" 배분 방식을 요구하는 조건부 항목이다. **현재는
사용 여부 자체가 결정되지 않았으므로 배분 규칙도 없다.** 배치안 비교의 분산을 줄이려면
CRN 이 유리하지만, 그 결정은 배차 엔진 구현과 함께 이뤄져야 한다.

결정되기 전까지 유일하게 고정된 사실은 **`SEED = 42` 단일 시드, PCG64 단일 스트림**이다.

### 재현성 규약

- 시드는 코드에 상수로 명시한다(`simulator.SEED`).
- 라이브러리 버전은 `requirements.txt` 로 고정하고, 검증된 조합을 위 표로 남긴다.
- 결과는 시드·설정과 함께 `outputs/` 에 저장한다.
- 캐시는 원본 mtime·크기(콜)와 산출 상수 crc32(이동시간)를 파일명에 박아 자동 무효화한다
  — 상수를 바꾸고 옛 테이블을 계속 쓰는 사고를 막는다.
  ([`preprocessing_log.md` A-8](preprocessing_log.md))
