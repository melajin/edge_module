# 보고서 ADXL345·FG V3 펌웨어

이 프로젝트는 최종보고서의 FG V3 검증 소스를 첫 실행 경로로 제공한다. [source_snapshot/](source_snapshot/)은 보고서 검증 사본과 해시를 대조한 원본을 보존하고, `src/`는 독립 PlatformIO 빌드 진입점이다.

| 입력·출력 | 보고서 구현 계약 |
|---|---|
| 가속도 | ADXL345 주소 `0x53`, 3축, 512표본, 400 Hz 설정 |
| 변환 | FULL_RES·±4 g, 256 LSB/g |
| I²C | SDA 21·SCL 22, 400 kHz |
| 회전 기준 | FG GPIO 25, 펄스 간격과 사용자 PPR |
| 계산 | 실제 표본 시각, 회전 동기 1× 진폭·위상, 정상 기준과 4/5 상태 |
| 출력 | 115200 baud, JSON 한 줄 |

```powershell
python -m platformio run
```

호스트 신호 검사와 Python/C 219회 정책 갱신 비교는 이 디렉터리에서 실행한다.

```powershell
python tools/run_host_checks.py
```

정상 기준 등록과 명령·상태는 [보고서 FG 해설](../../../docs/report-detail/05-fg-hardware.md)에 연결한다. 정상 팬 336창의 관측과 이번 호스트 검사·빌드는 [결과 해설](../../../docs/report-detail/06-results.md)에서 각각의 입력과 기록으로 확인한다.

## 보고서 이후의 측정 품질 기록

실행 소스 `src/`는 기존 판정 정책을 유지하면서 취득 시각·창 품질·창간 미관측 간격을 별도로 기록한다. 보고서 검증 원본인 `source_snapshot/`과 과거 평가 수치는 보존한다. 새 실행 소스의 빌드 크기와 검증 결과는 보고서 당시 결과와 구분한다.

추가 `acquisition` 필드, 수신 순번 상태, 구형 로그의 미확인 처리와 검증 범위는 [측정 품질 규칙 1](../../../docs/measurement-quality.md)에 설명한다. 수신 순번이 이어져도 센서의 연속 관측을 뜻하지 않는다.
