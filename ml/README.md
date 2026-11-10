# 보고서 모델과 공개 데이터 학습·검증

보고서의 101특징 정의와 모델 개발 경로는 [report_model/](report_model/), C 계산과 생성 모델 계수는 [../edge_module_c/report_ml/](../edge_module_c/report_ml/)에 있다. 400 Hz·400점의 선택 축 입력을 사용하는 모델의 특징 순서·표준화·마진은 [보고서 3절 해설](../docs/report-detail/03-ml-model.md)에서 상세히 설명한다. 보고서 CWRU 베어링 입력은 [../edge_module_c/report_cwru/](../edge_module_c/report_cwru/)의 12 kHz·4096점·6특징 경로에 연결한다.

## 기존 6특징 개발 기록

`ml/`은 CWRU와 MaFaulDa 공개 회전기계 파형을 읽어 특징을 만들고, 정상 학습형 이상 탐지와 파일 단위 지도 분류를 평가하는 Python 경로다. 계산·분할·평가 해설은 [최종보고서 상세 해설](../docs/report-detail/README.md), 결과의 단위와 분모는 [결과와 검증 범위](../docs/report-detail/06-results.md)에 연결되어 있다.

## 기존 CWRU·MaFaulDa 데이터와 6특징

기존 파이프라인의 분류 입력은 `rms`, `kurtosis`, `harmonic1_ratio`, `harmonic2_ratio`, `harmonic3_ratio`, `high_freq_ratio` 여섯 값이다. 고조파·고주파 특징은 대역 진폭합을 `f > 1 Hz`인 전체 FFT 진폭합으로 나눈 비율이다. 분류 train/test는 같은 파일의 창을 한 분할에 묶어 파일 단위로 나눈다.

| 데이터 | 원 파형과 추출 규모 | 분류 평가 분할 |
|---|---|---|
| CWRU | 12 kHz Drive-End, 40파일, 2,913창 | 학습 28파일, 시험 12파일·934창 |
| MaFaulDa | 50 kHz 8채널, 36파일, 1,044창 | 학습 25파일, 시험 11파일·319창 |

분류 test 창에서의 기록 정확도는 다음과 같다.

| 모델 | CWRU 934창 | MaFaulDa 319창 |
|---|---:|---:|
| Decision Tree, depth ≤ 4 | 85.8% | 71.5% |
| Logistic Regression | 91.2% | 61.8% |
| Random Forest, 200 trees | 82.2% | 72.4% |

## 정상 학습형 이상 탐지 기록

이상 탐지는 정상 파일의 앞 60% 창으로 기준을 학습하고, 정상 파일의 뒤 40%와 고장 파일의 창을 평가한다. 단일 창 1차 판정은 여섯 특징의 3σ `is_suspect`를 비교하며, MaFaulDa 속도별 기준은 12~61 Hz를 네 구간으로 나눈다.

| 판정 기록 | CWRU | MaFaulDa |
|---|---:|---:|
| 단일 창 3σ 정상 오경보율 | 4.8% | 0.0% |
| 단일 창 3σ 고장 탐지율 | 볼 100%, 내륜 100%, 외륜 100% | 불평형 74.4%, 정렬불량 56.9% |
| 회전 구간별 3σ 탐지율 | — | 불평형 90.2%, 정렬불량 82.5%; 정상 오경보율 0.0% |
| 5창 중 3창 확정 규칙 | 정상 확정 오경보 창 비율 1.8%, 고장 36/36파일 경보 | 정상 확정 오경보 창 비율 0.0%, 고장 16/24파일 경보 |

단일 창 탐지율은 창 단위 비율이며, `36/36`과 `16/24`는 파일별 확정 경보 여부다. 결과 JSON은 [`cwru_results.json`](results/cwru_results.json), [`mafaulda_results.json`](results/mafaulda_results.json)에 저장돼 있다.

## 보고서의 다른 평가 경로

위 표는 기존 `ml/` 파이프라인의 결과다. 보고서의 CWRU 잠금 시험, Mendeley 101특징 모델, 정상 팬 실측은 입력과 평가 단위가 다른 별도 실험이며 [실험 기록](../docs/experiments.md)에 정리되어 있다. 구현별 입력 조건은 [C·ESP32 안내](../edge_module_c/README.md)에 있다.

## 주요 파일과 실행 순서

| 경로 | 역할 |
|---|---|
| `dataset.py` | 공개 파일에서 원 파형 창·라벨·회전수·파일 그룹을 구성 |
| `features_real.py` | 여섯 특징 계산과 행렬 변환 |
| `train_models.py` | 정상 학습형 탐지 및 세 분류기 평가, 결과 JSON 저장 |
| `download_data.py` | CWRU·MaFaulDa 데이터 파일 선별 다운로드 |
| `export_c.py` | 학습된 작은 분류기를 C 헤더로 출력 |
| `make_figures.py` | 혼동행렬·특징 중요도·분포 그림 생성 |

재현 순서는 `download_data` → `train_models` → `make_figures` → `export_c`이며, 각 모듈은 `python -m ml.<모듈명>`으로 실행한다. 입력 계산은 [`dataset.py`](dataset.py)·[`features_real.py`](features_real.py)에서 [공통 Python 특징 함수](../src/feature_extraction.py)를 호출한다. 저장소 V2는 별도 [V2 기준 코드](../src/feature_extraction_v2.py)와 [V2 C 코어](../edge_module_c/README.md)에서 확인한다.
