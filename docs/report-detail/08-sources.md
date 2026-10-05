# 8. 출처·코드 대응표

아래의 [n]은 제출 PDF 21~22쪽의 인용 번호와 연결한다. 표는 공개 데이터·부품 문서, 프로젝트 평가 기록, 현재 코드 경로를 같은 번호 체계로 정리한다.

## 보고서 인용과 자료 경로

| PDF 번호 | 출처 | 사용 범위·자료 위치 |
|---|---|---|
| [1] | Case Western Reserve University, Bearing Data Center | CWRU 베어링 공개 파형. [공식 데이터 페이지](https://engineering.case.edu/bearingdatacenter/welcome) |
| [2] | Epson, Rotor Kit Vibration Dataset Download | Rotor Kit 불평형 파형 출처. 보고서 조건은 1,200 rpm, 3 kHz. [공식 데이터 페이지](https://www.epsondevice.com/sensing/en/dataset/index.html) |
| [3] | AI-Hub 회전체 고장 데이터 | 기존 V2 재생에 쓴 베어링·정렬 불량·벨트 파형. [공식 데이터 페이지](https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&dataSetSn=238&topMenu=100) |
| [4] | Analog Devices, ADXL345 Digital Accelerometer Data Sheet, Rev. G | 센서 범위와 대역폭 설정의 제조사 자료. [데이터시트 PDF](https://www.analog.com/media/en/technical-documentation/data-sheets/adxl345.pdf) |
| [5] | 엣지알리미 프로젝트 평가 기록, 2026-09-23~2026-09-26 | CWRU·Epson·AI-Hub 평가, 투표 규칙, B02~B04 시간 구간 대리 평가. 현재 위치: reports/final-report-evidence/faults.md, datasets-and-algorithms.md, misalignment-reconciled.json. |
| [6] | 엣지알리미 평가 JSON 산출물 | 제출 보고서 표 9·10의 결과 데이터 원자료. 파일 구성은 아래 추가 확보 원자료 표에 기록한다. |
| [7] | 엣지알리미 평가 스크립트 | 베어링·Rotor Kit 불평형·벨트 재발·모의진단 기준은 src/report_reference/의 Python 패키지와 연결한다. 원본 보관 위치: 워크스페이스 aihub_training/ |
| [8] | 엣지알리미 알고리즘·펌웨어 검증 보고서, 2026-09-26 | Python/C 정책 비교, 합성 C 검사, Python 검사, ESP32 빌드·해시 기록. 현재 위치: reports/algorithm-report-audit-2026-09-26/verification-report-ko.md, execution-findings.md, execution/ |
| [9] | 엣지알리미 FFT V2 C 구현과 Python 비교 | 특징·기준값·4/5 투표 계산. C 현재 위치: edge_module_c/core/em_config.h, em_fft.c, em_features.c, em_rotation.c, em_detector.c. Python 현재 위치: src/feature_extraction_v2.py, ml/features_real.py, ml/train_models.py, ml/export_c.py, tests/validate_v2.py, tools/export_v2_vectors.py. PDF의 512/400 조건과 저장소 C 기본값 1024/1000 Hz는 각 버전 설정으로 구분한다. |
| [10] | 엣지알리미 FG V3 검증 소스 사본, 2026-09-26 | 회전 동기 1× 구현과 검사·빌드 산출물. 현재 위치: edge_module_c/esp32/edge_alimi_adxl345_fg_report/source_snapshot/의 .ino, v3_signal.c/h, em_v3.c/h, test_signal.c, platformio.ini. |
| [11] | 엣지알리미 정상 팬 관측 기록과 요약 | 보고서의 336창 1× 반복성. 원 기록 파일명은 아래 추가 확보 원자료 표에 적었다. |
| [12] | 엣지알리미, 「제5회 창의혁신 공모전 최종보고서 초안(로그 변환 그림 원본)」 | 보고서 로그 변환 그림 원본. PDF 22쪽에 표시된 파일명은 아래 추가 확보 원자료 표와 같다. |
| [13] | 엣지알리미 CWRU 베어링 펌웨어 v1.8 검증, 2026-10-01~2026-10-02 | CWRU 펌웨어 분석·호스트 재생·빌드 기록. 현재 위치: reports/2026-10-01-cwru-firmware-integration-ko.md, edge_module_c/report_cwru/verification/result.json, build_result.json. |

## 별도 모델 개발 자료

| 자료 | 버전·식별 | 사용 경로 |
|---|---|---|
| [Mendeley Data, Mechanical faults in rotating machinery dataset (normal, unbalance, misalignment, looseness)](https://data.mendeley.com/datasets/zx8pfhdtnb/3) | Version 3, DOI 10.17632/zx8pfhdtnb.3, CC BY 4.0, 공개일 2023-07-28 | 4상태 101특징 모델 개발 자료. manifest는 Version 3을 기록한다. 관련 논문: [Brito et al., Fault Diagnosis using eXplainable AI, DOI 10.1016/j.eswa.2023.120860](https://doi.org/10.1016/j.eswa.2023.120860) |
| B02~B04 평가 입력 | 프로젝트 기록의 채널별 1.6초 취득 구간 | AC RMS와 4/5 시간 구간 평가. |

## 상세 해설과 구현 위치

| 상세 해설 | 연결 근거 | 현재 위치 |
|---|---|---|
| [시스템 흐름](01-system.md) | C 입력부터 계산·출력까지 | edge_module_c/README.md, edge_module_c/core/em_pipeline.c; Python V2 경로는 src/feature_extraction_v2.py, ml/features_real.py |
| [물리 근거 경로](02-physical-evidence.md) | V2 FFT와 물리 코어의 Hann·전력·기준 비교 | V2: edge_module_c/core/; 물리 코어: edge_module_c/report_physical/src/fault_evidence.c, edge_module_c/report_physical/README.md |
| [101특징 모델](03-ml-model.md) | 특징 추출·개발 분할·실시간 보간 | C: edge_module_c/report_ml/src/ml_reference.c, ml_live.c; Python: ml/report_model/a_features.py, a_group_develop.py; 연결 검증: edge_module_c/report_ml/tools/verify_ml.py |
| [결함별 물리 해석](04-fault-interpretation.md) | 회전 차수 후보, 별도 CWRU 분석 경로 | 물리 후보: edge_module_c/report_physical/; FG 1×: 보고서 검증 사본; 학습 자료: Mendeley v3 |
| [FG·하드웨어](05-fg-hardware.md) | FG V3 표본·위상·JSON 입력/출력 | 검증 사본: edge_module_c/esp32/edge_alimi_adxl345_fg_report/source_snapshot/; 2×/3× 실험: edge_module_c/report_ml/src/v3_signal.c |
| [결과](06-results.md) | 파일·창·취득 구간 평가와 빌드 수치 | edge_module_c/report_cwru/verification/result.json, reports/2026-09-23-b01-b04-ac-rms-bearing-ko.md, 제출 PDF |

## 저장소 식별과 문서 산출물

보고서·프로젝트 계약에 적힌 코드 저장소 주소는 [https://github.com/melajin/edge_module.git](https://github.com/melajin/edge_module.git)이며, 로컬 melajin remote URL과 일치한다. 상세 설명 산출물과 구현 파일 형식은 [README](README.md)에 안내했다.

## 추가 확보 원자료

평가 JSON의 기준 경로는 `F:/aihub_training_runs/`다.

| PDF 인용 | 원자료 | 연결 내용 |
|---|---|---|
| [6] | `bearing_continuous_simulation/result.json`<br>`rotorkit_imbalance_1x/result.json`<br>`belt_recurrence_diagnostic/belt_recurrence_diagnostic.json`<br>`bearing_ac_rms_b01_b04/summary.json`<br>`mock_exam_v2_improvement/diagnosis/*/diagnosis.json` | 평가 결과 JSON 4종과 정렬 불량 7개 설비 그룹의 진단 결과 |
| [11] | `test_steaystate_baseline10min.txt` | 정상 팬 336창 및 회전 1× 원기록 |
| [12] | `제5회 창의혁신 공모전 최종보고서 (4).docx` | 보고서 로그 변환 그림의 출처 원본 |
