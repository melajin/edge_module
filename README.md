# 엣지알리미 (Edge Alimi)

소형 팬과 모터의 진동을 장치 안에서 분석해, 먼저 점검할 설비와 그 근거를 알려 주는 엣지 모듈이다. ADXL345로 3축 진동을 재고 팬의 FG 신호로 회전 기준을 잡으며, ESP32가 정상 운전 때 저장한 기준과 현재 진동을 비교한다.

이 저장소는 제5회 창의혁신 공모전 최종보고서의 알고리즘과 ESP32 펌웨어 코드다.

## 지금 상태

- **보고서 결과**: 공개 데이터 재생, Python/C 계산 일치, ESP32 빌드, 정상 팬 실측까지 확인했다. 고장 상태의 실제 설비 측정은 없다.
- **보고서 이후(`postreport_v2`)**: 측정 품질 기록과 정상 기준 승인 절차를 추가했다. PC 검사와 빌드는 통과했고, 장치 업로드와 실측은 아직 하지 않았다.

실험별 방법과 확인된 사실, 확인하지 못한 것은 **[실험 기록](docs/experiments.md)**에 있다.

## 무엇을 보려면 어디로

| 알고 싶은 것 | 문서 |
|---|---|
| 어떤 실험을 했고 무엇이 확인됐나 | [실험 기록](docs/experiments.md) |
| 수식과 계산 과정 | [보고서 상세 해설](docs/report-detail/README.md) |
| 결과 수치의 분자와 분모 | [결과와 검증 범위](docs/report-detail/06-results.md) |
| 보고서 인용과 원자료 위치 | [출처·코드 대응표](docs/report-detail/08-sources.md) |
| 펌웨어 빌드와 구현별 입력 조건 | [C·ESP32 안내](edge_module_c/README.md) |
| 보고서 이후 무엇이 바뀌었나 | [후속 개선 기록](docs/post-report-improvements.md) |
| 정상 기준 등록·승인·복원 명령 | [정상 기준 관리](docs/baseline-lifecycle.md) |
| 장치에 올리기 전 준비 | [ESP32 설치 준비](docs/firmware-installation.md) |

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

## 실행

Python 의존성을 설치한다.

```sh
python -m pip install -r requirements.txt
```

보고서 펌웨어는 PlatformIO로 빌드한다. 호스트 C 비교에는 GCC를 쓴다.

```powershell
cd edge_module_c/esp32/edge_alimi_adxl345_fg_report
python -m platformio run
```

핀·센서 설정과 JSON 출력 필드는 [C·ESP32 안내](edge_module_c/README.md)에 있다. 기록을 읽는 화면은 [보고서 첫 화면](docs/index.html)이고, 이전 3σ 시뮬레이터는 [V2 이력 화면](docs/legacy-v2-dashboard.html)으로 보존한다.

## 이전 구현

보고서 경로와 입력·특징·평가 단위가 다른 구현이다. 비교와 이력을 위해 보존한다.

| 경로 | 내용 |
|---|---|
| [edge_module_c/core/](edge_module_c/core/) | 이전 V2 C99 코어: 1024점·1000 Hz·로그 3σ·4/5 |
| [edge_module_c/esp32/edge_alimi/](edge_module_c/esp32/edge_alimi/) | 이전 MPU-6050 V2 펌웨어와 HTTP 콘솔 |
| [c/](c/README.md) | `N=256`, `fs=1000 Hz`의 독립 C99 API와 Python 기준 벡터 검증기 |
| [src/feature_extraction_v2.py](src/feature_extraction_v2.py) | V2의 Python 특징 기준 계산 |
| [ml/](ml/README.md) | CWRU·MaFaulDa 공개 데이터의 기존 6특징 학습·평가 |
