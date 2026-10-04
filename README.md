# 엣지알리미 (Edge Alimi)

엣지알리미는 회전 설비의 진동 변화를 계산해 점검에 필요한 정보와 근거를 제공하는 엣지 시스템이다. 현재 공개 저장소에는 V2의 C99 계산 코어, ESP32 펌웨어, Python 기준 계산과 이전 공개 데이터 학습 경로가 있다. 구현별 입력과 기준선 계산은 버전별 계약으로 관리한다.

최종보고서의 계산 흐름과 수식은 [상세 해설](docs/report-detail/README.md)에서 읽을 수 있다. 보고서에 연결된 코드 위치와 증거 종류는 [출처·코드 대응표](docs/report-detail/08-sources.md)에 정리되어 있다.

## 현재 계산 경로

| 경로 | 입력·계산 계약 | 역할과 근거 |
|---|---|---|
| 저장소 V2 | `N=1024`, 설정 `fs=1000 Hz`, MPU-6050 단일 축 입력(ESP32 기본 X), `rms`와 네 대역 비율 | C 구현은 [공통 코어](edge_module_c/core/), 펌웨어는 [스케치](edge_module_c/esp32/edge_alimi/edge_alimi.ino), Python 기준 계산은 [V2 특징 코드](src/feature_extraction_v2.py)에서 이어진다. 정상 기준은 로그 특징의 평균·표본 표준편차이며 3σ와 최근 5창 중 4창 규칙을 사용한다. |
| 물리 근거 코어 | `N=512`, X/Y/Z 가속도 입력, 창별 실제 `fs`와 FG 회전 주파수. 기본 신뢰 상한은 `min(160 Hz, 0.4·fs)` | 336개 정상 창으로 로그 중앙값·MAD 기준을 만들고 회전 차수 대역과 시간 형상에서 후보 근거를 계산한다. 최신 원본은 워크스페이스의 `fault_evidence_core/`이다. |
| 101특징 학습 모델 | 선택 축 400점·400 Hz 특징, 101개 값과 네 클래스 선형 마진 | 모델 학습 원본은 `fault_type_90_mechanical/`, 실시간 C 어댑터는 `firmware_ml_evidence_live/`이다. 실시간 입력은 512개 표본을 시간 보간해 400점으로 만들며 기본 축은 X, 시리얼은 230400 baud다. 보고서의 5-fold OOF 개발 기록은 1,200개 기록에서 정확도 93.67%다. |
| CWRU 베어링 모델 | `N=4096`, `fs=12000 Hz`, 6특징·네 클래스 | 별도 통합 경로 `firmware_cwru_integration/`의 공개 파형 호스트 재생에서 특징·점수 비교는 640창, 4/5 투표 평가는 초기 이력 40창을 제외한 600창으로 기록돼 있다. |

저장소 V2의 다섯 특징은 `rms`, `harmonic1_ratio`, `harmonic2_ratio`, `harmonic3_ratio`, `high_freq_ratio`다. `rms`는 절대 진폭을 나타내고, 각 비율은 해당 대역의 진폭 제곱합을 DC 제외 전체 FFT 진폭 제곱합으로 나눈 값이다.

## 저장소 구성

| 경로 | 내용 |
|---|---|
| [edge_module_c/](edge_module_c/README.md) | V2 C99 공통 코어와 ESP32 Arduino 펌웨어, 합성 신호 시험 및 V2 기준 벡터 비교 |
| [c/](c/README.md) | `N=256`, `fs=1000 Hz` 설정을 가진 독립 C99 공개 API와 Python 기준 벡터 검증기 |
| [src/feature_extraction_v2.py](src/feature_extraction_v2.py) | 저장소 V2의 Python 특징 기준 계산 |
| [ml/](ml/README.md) | CWRU·MaFaulDa 공개 데이터의 기존 6특징 학습·평가 파이프라인 |
| [docs/report-detail/](docs/report-detail/README.md) | 보고서 시스템 흐름, 계산식, 평가 단위, 출처와 코드 대응 |

`c/`, 저장소 V2, `ml/`, 물리 근거 코어, 101특징 실시간 모델, CWRU 통합은 입력·특징·평가 단위가 서로 다른 구현 경로다. 보고서의 세부 표에는 각 경로의 표본 조건과 평가 분모를 함께 기록한다.

## 증거와 작업 공간 원본

| 증거·구현 | 위치 | 기록 범위 |
|---|---|---|
| 정상 팬 FG 관측·V3 검증 사본 | `D:\obsidian\claude\obsidian_export\Edge_module_folder\reports\algorithm-report-audit-2026-09-26\execution\firmware-build\` | 보고서용 1× 동기 경로, 시리얼 115200 baud, 정상 팬 336창 기록 |
| 물리 근거 원본 | `D:\obsidian\claude\obsidian_export\Edge_module_folder\fault_evidence_core\` | 512점 3축 입력, 로그 중앙값/MAD 기준, 신뢰 상한 160 Hz |
| 101특징 학습·실시간 원본 | `D:\obsidian\claude\obsidian_export\Edge_module_folder\fault_type_90_mechanical\` 및 `D:\obsidian\claude\obsidian_export\Edge_module_folder\firmware_ml_evidence_live\` | 학습 특징 정의와 230400 baud 실시간 어댑터 |
| CWRU 통합·호스트 평가 | `D:\obsidian\claude\obsidian_export\Edge_module_folder\firmware_cwru_integration\` | 12 kHz·4096점·6특징 경로, 640창 비교와 600창 4/5 판정 |

증거 표기는 데이터시트 사양, 코드 설정값, 정상 팬 운전 관측, 공개 파형 호스트 재생, 합성 신호 검사, 기록된 ESP32 빌드로 나뉜다. 세부 조건과 분자·분모는 [결과와 검증 범위](docs/report-detail/06-results.md)에서 확인할 수 있다.

## 추가 확보 원자료

원자료의 버전과 사용 경로는 [출처·코드 대응표](docs/report-detail/08-sources.md)에 정리되어 있다.

| 보고서 인용 | 원자료 | 연결 내용 |
|---|---|---|
| [6] | `F:/aihub_training_runs/bearing_continuous_simulation/result.json`, `rotorkit_imbalance_1x/result.json`, `belt_recurrence_diagnostic/belt_recurrence_diagnostic.json`, `bearing_ac_rms_b01_b04/summary.json`, `mock_exam_v2_improvement/diagnosis/*/diagnosis.json` | 평가 결과 JSON과 정렬 불량 설비 그룹 진단 결과 |
| [11] | `test_steaystate_baseline10min.txt` | 정상 팬 336창과 회전 1× 관측 원기록 |
| [12] | `제5회 창의혁신 공모전 최종보고서 (4).docx` | 로그 변환 그림 원본 |
