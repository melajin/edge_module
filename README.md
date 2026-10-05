# 엣지알리미 (Edge Alimi)

엣지알리미는 최종보고서의 순서에 따라 센서 입력, 계산식, C·Python 구현과 검증 결과를 연결하는 저장소다. ADXL345의 3축 진동과 팬 FG 회전 기준을 입력으로 사용하며, 보고서에서 압축한 계산 과정은 상세 해설에서 풀어쓴다.

최종보고서의 계산 흐름과 수식은 [상세 해설](docs/report-detail/README.md)에서 읽을 수 있다. 보고서에 연결된 코드 위치와 증거 종류는 [출처·코드 대응표](docs/report-detail/08-sources.md)에 정리되어 있다.

## 보고서 순서와 구현

| 보고서 순서 | 상세 해설 | 구현 |
|---|---|---|
| 전체 시스템 | [1. 시스템 흐름](docs/report-detail/01-system.md) | [ADXL345·FG 펌웨어](edge_module_c/esp32/edge_alimi_adxl345_fg_report/) |
| 물리 근거 계산 | [2. 물리 근거](docs/report-detail/02-physical-evidence.md) | [물리 C 코어](edge_module_c/report_physical/) |
| 101특징 학습 모델 | [3. 특징·모델](docs/report-detail/03-ml-model.md) | [C 특징·추론](edge_module_c/report_ml/), [Python 특징](ml/report_model/) |
| 결함별 해석 | [4. 결함별 물리 해석](docs/report-detail/04-fault-interpretation.md) | [물리 C 코어](edge_module_c/report_physical/), [CWRU 베어링 경로](edge_module_c/report_cwru/) |
| FG·하드웨어 | [5. FG와 장치](docs/report-detail/05-fg-hardware.md) | [보고서 FG V3](edge_module_c/esp32/edge_alimi_adxl345_fg_report/), [Python 기준 계산](src/report_reference/) |
| 평가 결과 | [6. 결과·검증](docs/report-detail/06-results.md) | [검사 도구](tools/), [테스트](tests/) |
| 기대효과 | [7. 운영 지표](docs/report-detail/07-effects-and-limits.md) | 점검·기록·해석 흐름 |
| 출처 | [8. 출처·코드 대응](docs/report-detail/08-sources.md) | 보고서 인용과 원자료 |

## 첫 실행 경로

보고서 펌웨어는 `edge_module_c/esp32/edge_alimi_adxl345_fg_report/`에서 빌드한다. ADXL345 주소는 `0x53`, 입력은 3축 512표본, 설정 데이터율은 400 Hz이며 FG GPIO 25의 펄스와 PPR로 회전 기준을 계산한다. SDA 21·SCL 22, I²C 400 kHz와 시리얼 115200 baud는 보고서 검증 사본의 설정이다. 소스 원문과 빌드 진입점을 함께 보관하며 정상 기준·동기 1× 진폭·위상·4/5 상태를 JSON으로 출력한다.

실행 명령과 입력·출력 계약은 [C·ESP32 안내](edge_module_c/README.md)에 연결한다. [보고서 첫 화면 소스](docs/index.html)는 보고서 목차와 기록 읽기를 제공하고, 기존 3σ 시뮬레이터는 [V2 이력 화면](docs/legacy-v2-dashboard.html)으로 이어진다.

Python 의존성은 `python -m pip install -r requirements.txt`로 설치하고, 펌웨어 빌드는 PlatformIO, 호스트 C 비교는 GCC를 사용한다. 각 계산 묶음의 README에서 실행 명령과 입력 자료를 이어 확인한다.

## 계산 경로별 입력 계약

| 경로 | 입력·계산 계약 | 역할과 근거 |
|---|---|---|
| 보고서 FG V3 | ADXL345 3축 `N=512`, 설정 400 Hz, 표본 시각과 FG 펄스 | [보고서 펌웨어](edge_module_c/esp32/edge_alimi_adxl345_fg_report/)의 동기 1× 진폭·위상과 4/5 상태, 시리얼 115200 baud |
| 물리 근거 코어 | `N=512`, X/Y/Z 가속도 입력, 창별 실제 `fs`와 FG 회전 주파수. 기본 신뢰 상한은 `min(160 Hz, 0.4·fs)` | [물리 C 코어](edge_module_c/report_physical/)에서 336개 정상 창의 로그 중앙값·MAD 기준과 회전 차수 후보 근거를 계산한다. |
| 101특징 학습 모델 | 선택 축 400점·400 Hz 특징, 101개 값과 네 클래스 선형 마진 | [Python 특징](ml/report_model/)과 [C 어댑터](edge_module_c/report_ml/)를 연결한다. 실시간 입력은 512개 표본을 시간 보간해 400점으로 만들며 기본 축은 X다. 개발 OOF 기록은 1,200개 기록에서 정확도 93.67%이며, 확장 실시간 버전의 시리얼 설정은 230400 baud다. |
| CWRU 베어링 모델 | `N=4096`, `fs=12000 Hz`, 6특징·네 클래스 | [CWRU 경로](edge_module_c/report_cwru/)의 공개 파형 호스트 재생 기록은 특징·점수 비교 640창과 초기 이력 40창 뒤 600창의 4/5 평가다. |

저장소 V2의 다섯 특징은 `rms`, `harmonic1_ratio`, `harmonic2_ratio`, `harmonic3_ratio`, `high_freq_ratio`다. `rms`는 절대 진폭을 나타내고, 각 비율은 해당 대역의 진폭 제곱합을 DC 제외 전체 FFT 진폭 제곱합으로 나눈 값이다.

## 이전 구현과 비교 자료

| 경로 | 내용 |
|---|---|
| [edge_module_c/core/](edge_module_c/core/) | 이전 V2 C99 코어: 1024점·1000 Hz·로그 3σ·4/5 |
| [edge_module_c/esp32/edge_alimi/](edge_module_c/esp32/edge_alimi/) | 이전 MPU-6050 V2 펌웨어와 HTTP 콘솔 |
| [c/](c/README.md) | `N=256`, `fs=1000 Hz` 설정을 가진 독립 C99 공개 API와 Python 기준 벡터 검증기 |
| [src/feature_extraction_v2.py](src/feature_extraction_v2.py) | 저장소 V2의 Python 특징 기준 계산 |
| [ml/](ml/README.md) | CWRU·MaFaulDa 공개 데이터의 기존 6특징 학습·평가 파이프라인 |
| [docs/report-detail/](docs/report-detail/README.md) | 보고서 시스템 흐름, 계산식, 평가 단위, 출처와 코드 대응 |

`c/`, 저장소 V2, `ml/`, 물리 근거 코어, 101특징 실시간 모델, CWRU 통합은 입력·특징·평가 단위가 서로 다른 구현 경로다. 보고서의 세부 표에는 각 경로의 표본 조건과 평가 분모를 함께 기록한다.

## 보고서 근거와 구현 원본

| 증거·구현 | 위치 | 기록 범위 |
|---|---|---|
| FG V3 검증 소스 사본 | [source_snapshot/](edge_module_c/esp32/edge_alimi_adxl345_fg_report/source_snapshot/) | 보고서 원본과 해시 대조한 1× 동기 경로, 시리얼 115200 baud |
| 물리 근거 구현 | [report_physical/](edge_module_c/report_physical/) | 512점 3축 입력, 로그 중앙값/MAD 기준, 신뢰 상한 160 Hz |
| 101특징 학습·C 구현 | [report_model/](ml/report_model/), [report_ml/](edge_module_c/report_ml/) | Python 특징과 생성 계수·C 어댑터 |
| CWRU 통합·호스트 평가 | [report_cwru/](edge_module_c/report_cwru/) | 12 kHz·4096점·6특징, 보고서 640창 비교와 600창 4/5 기록 |

증거 표기는 데이터시트 사양, 코드 설정값, 정상 팬 운전 관측, 공개 파형 호스트 재생, 합성 신호 검사, 기록된 ESP32 빌드로 나뉜다. 세부 조건과 분자·분모는 [결과와 검증 범위](docs/report-detail/06-results.md)에서 확인할 수 있다.

## 추가 확보 원자료

원자료의 버전과 사용 경로는 [출처·코드 대응표](docs/report-detail/08-sources.md)에 정리되어 있다.

| 보고서 인용 | 원자료 | 연결 내용 |
|---|---|---|
| [6] | `F:/aihub_training_runs/bearing_continuous_simulation/result.json`, `rotorkit_imbalance_1x/result.json`, `belt_recurrence_diagnostic/belt_recurrence_diagnostic.json`, `bearing_ac_rms_b01_b04/summary.json`, `mock_exam_v2_improvement/diagnosis/*/diagnosis.json` | 평가 결과 JSON과 정렬 불량 설비 그룹 진단 결과 |
| [11] | `test_steaystate_baseline10min.txt` | 정상 팬 336창과 회전 1× 관측 원기록 |
| [12] | `제5회 창의혁신 공모전 최종보고서 (4).docx` | 로그 변환 그림 원본 |
