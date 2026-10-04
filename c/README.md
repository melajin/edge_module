# `c/` 독립 C99 구현

`c/`는 신호 창에서 특징과 이상 판정을 계산하는 독립 C99 API다. 센서 드라이버 대신 호출자가 표본 배열을 전달하므로 PC 검증과 ESP-IDF 계열 프로젝트에서 같은 계산 함수를 사용할 수 있다. 현재 V2 코어·보고서 연결은 [상세 해설](../docs/report-detail/README.md), 현재 구현 간 차이는 [상위 README](../README.md)에 정리되어 있다.

## 입력과 계산 계약

| 항목 | 현재 코드 |
|---|---|
| 표본 창 | `EA_WINDOW_SIZE=256`, `EA_SAMPLE_RATE=1000 Hz` |
| 특징 5개 | RMS와 1×·2×·3×·고주파 대역의 진폭합 |
| 회전 추정 | 30~70 Hz 피크 탐색, 이전 추정과 비교하는 스트림 체인, 최대 점프 `5 Hz` |
| 정상 기준 | 원래 특징 공간의 평균과 모집단 표준편차(`n`으로 나눔) |
| 1차 판정 | 특징별 평균에서 `3σ` 이탈 여부 |
| 확정 판정 | 5창 원형 버퍼에서 의심 비율 `0.6`, 즉 버퍼 충족 후 3/5창 |
| 드리프트 | 최초 평균을 기준으로 특징 변화를 추적하며 재학습 시 현재 평균·표준편차를 갱신 |

회전 추정 독립 호출은 `EA_NO_ESTIMATE`(NAN)를 전달해 현재 창의 피크로 회전 기준을 새로 계산한다. `ea_extract_features(..., independent=true)`도 현재 창의 피크로 회전 기준을 정하며, `independent=false`는 호출자가 보관한 회전 추정값을 다음 창에 전달한다. 체인 경로에서 새 후보가 이전 값보다 5 Hz 넘게 이동하면 이전 추정값을 유지한다. V2 코어는 같은 위치의 변화 후보가 설정된 횟수로 이어질 때 새 회전 기준을 재수용한다.

## 파일과 공개 API

| 경로·함수 | 역할 |
|---|---|
| [`edgealimi.h`](edgealimi.h) | 표본·특징 상수, 데이터 구조, C99 API 선언 |
| [`edgealimi.c`](edgealimi.c) | FFT, RMS·대역 특징, 회전 추정, baseline 학습·판정 |
| `ea_compute_fft_magnitudes()` | 256점 입력의 단측 진폭 스펙트럼 계산 |
| `ea_estimate_rotation_hz()` | 이전 회전 추정값을 인자로 받는 회전 피크 추정 |
| `ea_extract_features()` | 다섯 특징과 독립·스트림 체인 회전 추정 |
| `ea_detector_fit()` / `ea_detector_judge()` | 특징 기준 학습, 3σ 이탈·연속성·드리프트 정보 출력 |
| [`validate_main.c`](validate_main.c) | Python 기준 벡터로 특징·회전·평균·표준편차·판정 비교 |
| `../esp32/edgealimi_v2_realsensor/test_vectors.h` | 검증 하니스가 읽는 Python 기준 벡터 |

`validate_main.c`는 각 기준 파형에 `EA_NO_ESTIMATE`를 전달해 창별 회전 추정을 독립 비교하고, Python에서 생성한 특징 벡터를 기준으로 baseline 평균·모집단 표준편차와 3σ 불리언을 대조한다. 저장된 실행 기록은 56/56 비교 통과다. 특징 비교는 상대오차 2%(작은 값은 절대오차 바닥 `1e-4`), 회전 주파수는 `0.01 Hz`, 판정 불리언은 완전 일치를 사용한다. 상태 확인 구간은 5창 버퍼 충족 전과 후, 재학습 전후의 원래 baseline 평균 보존을 포함한다.

## V2 및 Python 경로와의 관계

| 경로 | 설정·특징·통계 |
|---|---|
| 이 `c/` API | 256점, RMS와 네 대역 진폭합, 선형 평균·모집단 표준편차, 최근 5창 3/5 |
| [V2 C 코어](../edge_module_c/README.md) | 1024점·1000 Hz, RMS와 DC 제외 FFT 에너지 분모의 네 비율, 로그 평균·표본 표준편차, 최근 5창 4/5 |
| [Python V2 기준](../src/feature_extraction_v2.py) | V2 C 코어의 동일한 다섯 특징 계산 순서와 입력 계약 |
| [공개 데이터 학습](../ml/README.md) | CWRU·MaFaulDa 6특징 평가와 파일·창 단위 결과 |

따라서 이 폴더의 `EA_CONFIRM_RATIO=0.6`은 V2의 4/5 규칙과 별도 설정으로 읽는다. V2 특징 이름과 입력, 기준 통계는 [시스템 구현 버전 표](../docs/report-detail/README.md)에서 함께 비교할 수 있다.

## PC에서 기준 벡터 대조

저장소 루트에서 GCC 또는 MinGW를 사용해 C99 검증기를 컴파일한다.

```sh
gcc -std=c99 -O2 -Wall c/validate_main.c c/edgealimi.c -o validate -lm
./validate
```

이 하니스는 C 소스와 Python에서 생성된 벡터를 비교하는 호스트 소프트웨어 검사다. 새 검사 실행 기록과 실제 ESP32 장치 관측은 [작업 결과](../docs/report-detail/06-results.md)의 증거 종류별 기록에서 확인한다.
