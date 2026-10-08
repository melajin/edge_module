# 보고서 101특징 C 계산

보고서의 선택 축 400점·400 Hz 특징과 네 클래스 마진을 계산하는 묶음이다. `src/ml_reference.c`가 101특징을 만들고 생성 모델 헤더의 계수로 점수를 계산하며, `src/ml_live.c`가 실제 시각을 가진 512표본을 400점으로 보간한다. 점수는 다섯 fold의 평균 선형 SVM 마진이다.

특징 순서와 로그 변환·표준화·계수 접기는 [101특징 상세 해설](../../docs/report-detail/03-ml-model.md), Python 기준은 [ml/report_model/](../../ml/report_model/)에 있다. 20×20 DFT와 생성된 삼각·Hann 표를 사용한다. 최신 확장 `v3_signal.c`의 2×·3×는 보고서 FG 1× 사본과 구분해 제공한다.

| 경로 | 역할 |
|---|---|
| [src/](src/) | 특징·보간·생성 모델 계수·확장 신호 계산 |
| [tools/](tools/) | Python 비교와 모델 계수 내보내기 |
| [verification/](verification/) | 출처 식별과 기존 비교 기록 |

모델 개발 OOF 1,200개 기록의 정확도 93.67%는 보고서의 개발 평가다. 호스트 수치 비교와 실제 입력 가용 상태는 각각의 검사 기록에 연결하며, 생성 모델 출처와 입력 자료 경로를 함께 보존한다.

이 디렉터리의 `python tools/verify_ml.py`는 생성 헤더를 C로 빌드하고 합성 400점 입력의 101특징·네 점수와 상수 입력 상태를 확인한다. 개발 원신호·fold 모델을 이용한 수치 비교는 같은 도구의 입력 인자로 연결한다.

## 보고서 이후의 자원 오류 진단

`ml_reference_workspace_bytes()`는 구현의 `sizeof` 기준 heap 작업 공간 크기를 반환하고, `ml_reference_resource_state()`는 `ML_REFERENCE_UNINITIALIZED`, `ML_REFERENCE_ALLOCATION_FAILED`, `ML_REFERENCE_READY`를 구분한다. 조회는 할당을 수행하지 않는다. 초기화는 호출자가 `ml_reference_init()`으로 수행하며 실패 후 재시도할 수 있다. 준비가 끝난 뒤 반복 초기화는 새로 할당하지 않는다. 작업 공간은 프로세스 수명 동안 유지되며, 정적 버퍼와 함께 쓰므로 초기화·추출·live 추론·상태 조회의 동시 접근은 호출자가 직렬화해야 한다.

`ml_live_run()`은 매창 자동 할당하지 않는다. 기존 입력 검사를 통과한 뒤 자원 상태가 준비되지 않았다면 `ml_workspace_uninitialized` 또는 `ml_workspace_allocation_failed`를 반환한다. 이때 `label=-1`, 점수는 `NaN`이다. 입력 자체의 오류는 기존 입력 오류 사유로 남긴다. 계산 가능한 주파수 대역의 `order_mask`는 모델 실행 성공이나 센서의 물리적 관측 보장을 뜻하지 않는다.

보간에 쓰는 시각은 MCU 센서 읽기 시각이다. 이 저장소의 FG 취득 경로에서는 읽기 시작에 기록하며 센서 내부 변환 시각을 직접 측정한 값이 아니다. 선형 보간과 공개 학습 자료의 `resample_poly`는 서로 다른 전처리다. 성공 경로도 `experimental_domain_mismatch`를 유지하며, 네 점수는 확률이 아닌 평균 선형 SVM 마진이다.

이번 변경은 초기화 실패를 신호 오류와 구분하는 운영 개선이다. 101특징 수식·순서, 보간, 생성 모델 계수와 기존 평가 기록은 유지한다. `verification/`의 수치는 역사적 검사 기록으로 남긴다. 새 자원 검사는 결과를 임시 폴더에서만 만들며 다음처럼 실행한다.

```powershell
python tests/test_ml_resources.py --baseline-ref 52a7df241dc9508f01509f8e322a29212eae8897
```

위 기준 버전과 합성 입력의 특징·마진·라벨을 비교하며, 초기화 미호출·할당 실패·재시도·반복 초기화도 검사한다. 호스트에서 조회한 작업 공간은 33,816바이트였으며, 타깃의 실제 크기는 조회 API와 해당 빌드로 확인해야 한다. 이 검사는 실물 고장 분류 성능이나 전체 학습 자료의 재평가가 아니다. [후속 개선 범위](../../docs/post-report-improvements.md)를 함께 확인한다.
