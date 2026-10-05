# 보고서 Python 기준 계산

보고서에 대응하는 기준 계산은 [report_reference/](report_reference/)에서 시작한다. 보고서 FG C 구현과 동일 입력·정책을 대조하고, [101특징 Python 정의](../ml/report_model/)와 [CWRU 기준](../edge_module_c/report_cwru/)를 각 입력 조건에 연결한다.

| 보고서 경로 | 입력 | 설명 |
|---|---|---|
| FG V3 | 3축 512표본·시각·FG·PPR | [동기 1×·위상·상태](../docs/report-detail/05-fg-hardware.md) |
| 101특징 | 선택 축 400점·400 Hz·회전 기준 | [특징 순서·표준화·마진](../docs/report-detail/03-ml-model.md) |
| CWRU | 12 kHz·4096점 | [6특징·4/5 평가](../docs/report-detail/06-results.md) |

## 이전 비교 구현

`feature_extraction_v2.py`는 1024점·1000 Hz의 이전 V2 특징을 계산하며, `feature_extraction.py`·`signal_generator.py`·`baseline_detector.py`·`reference_comparison.py`는 초기 합성 입력과 정상 기준의 비교 경로다. 이 파일들의 기본값과 평가 기록은 해당 버전으로 보존하고, 보고서 계산은 위의 기준 경로에서 읽는다.
