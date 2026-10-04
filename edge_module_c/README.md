# V2 C 코어와 ESP32 펌웨어

이 폴더는 엣지알리미 V2의 공통 C 계산 코어와 ESP32 Arduino 펌웨어를 담는다. 시스템 전체의 데이터 흐름과 보고서 대응은 [최종보고서 상세 해설](../docs/report-detail/README.md), 구현 버전과 외부 원본 위치는 [상위 README](../README.md)에 연결되어 있다.

## 데이터 흐름과 계산 계약

```text
MPU-6050 단일 축 표본
  → 1024점 창·평균 제거·RMS·대칭 Hann·FFT
  → 회전 주파수 추정·다섯 특징 계산
  → 로그 평균·표본 표준편차 기준선과 비교
  → 3σ 1차 이탈·최근 5창 중 4창 판정
  → JSON·시리얼·대시보드·경보 GPIO
```

| 항목 | V2 설정·계산 |
|---|---|
| 표본 창 | `EM_FFT_SIZE=1024`, 펌웨어 설정 `SAMPLE_RATE_HZ=1000 Hz` |
| 입력 축 | ESP32 기본 `ACCEL_AXIS=0`(X); 설정에서 Y 또는 Z 축 선택 |
| 특징 | `rms` 절대량, `harmonic1_ratio`, `harmonic2_ratio`, `harmonic3_ratio`, `high_freq_ratio`; 각 비율은 대역 진폭 제곱합 / DC 제외 전체 FFT 진폭 제곱합 |
| 대역 | 회전 기준 1×·2×·3×와 센서 대역 내 고주파. 코드 기본 `sensor_bw_hz=260 Hz`, HF 상한은 `min(fs/2, sensor_bw_hz−5 Hz)` |
| 기준선 | `log(x + 1e-6)` 특징의 평균·표본 표준편차, 3σ 비교 |
| 확정 정책 | 최근 5창 중 4창 이상 1차 이탈 |
| 학습 요청 | 기본 120창. 학습은 펌웨어의 `/learn` API 또는 시리얼 명령으로 시작 |

V2 계산 상세는 [물리 근거 경로 해설](../docs/report-detail/02-physical-evidence.md)과 [구현 버전 안내](../docs/report-detail/README.md)에 정리되어 있다.

## 폴더와 인터페이스

| 경로 | 역할 |
|---|---|
| `core/em_config.[ch]` | FFT·표본율·회전·특징·판정 설정과 이름 |
| `core/em_fft.[ch]` | 평균 제거, RMS, 대칭 Hann, FFT 스펙트럼 |
| `core/em_rotation.[ch]` | 회전 후보 탐색, 급격한 후보 변화 처리와 재수용 |
| `core/em_features.[ch]` | V2의 RMS와 네 대역 비율 계산 |
| `core/em_detector.[ch]` | 로그 공간 baseline, 3σ, 4/5 확인, 드리프트와 스냅샷 |
| `core/em_pipeline.[ch]` | 창별 계산, 학습·감시 흐름과 JSON 직렬화 |
| `esp32/edge_alimi/edge_alimi.ino` | 센서 취득, 현장 계산, NVS 기준선, 네트워크 API와 출력 |
| `pc_test/main.c` | 합성 신호 입력 기반 거동 검사 |
| `pc_test/validate_v2.c` | Python V2 기준 벡터와 C 특징·로그 값의 수치 비교 |
| `viz/dashboard.html`, `tools/embed_dashboard.py` | 대시보드 원본과 펌웨어 내장 자산 생성 |
| `pi/pi_server.py`, `pi/pi_logger.py` | 결과 수집·JSONL 이력·화면 제공 |

펌웨어의 네트워크 인터페이스는 `GET /`, `GET /data`, `GET /health`, `POST /learn?n=120`, `POST /config?rpm=...`, `POST /baseline/clear`이며, 시리얼 `l`, `r<rpm>`, `c`는 학습·정격 변경·기준선 삭제를 요청한다. `API_KEY` 설정 시 상태 변경 API는 요청의 `key`를 확인한다. 확정 판정은 JSON·시리얼과 경보 GPIO로 전달된다.

정상 기준은 사용자가 설비의 정상 운전을 확인한 뒤 학습을 시작해 만든다. 정상 센서 상태에서 가동으로 판별된 초기 5창은 워밍업으로 두고 기본 120창을 학습한다. 학습 완료 기준선은 NVS에 저장하며 부팅 때 저장 정격 RPM과 현재 설정이 0.5 RPM 이내로 일치하면 복원한다. 판정 버퍼가 5창을 채운 뒤 최근 5창 중 4창이 3σ를 벗어나면 확정 이상으로 출력한다. 정지 상태에서는 학습·판정을 멈춘다. 정상 센서 상태의 창으로 학습·판정을 진행하고, 취득 실패가 허용 수를 넘으면 해당 창의 판정 대신 `sfault` 상태 JSON을 출력하며, 클리핑이 1%를 넘으면 `clip` 정보를 판정 결과와 함께 출력한다. 드리프트는 로그 특징의 최근 60창 평균을 최초 정상 평균과 비교하며, 기준선을 다시 학습해도 최초 평균을 보존한다.

## 실행 기록과 다른 구현 경로

`pc_test/main.c`는 합성 파형을 이용해 회전 변화, 정지·재가동, 기준선 보존 등 C 코어의 동작을 살핀다. `pc_test/validate_v2.c`는 Python V2 기준 벡터와 특징 및 로그 변환 결과를 수치로 비교한다. 보고서의 실행 환경과 기록값은 [결과와 검증 범위](../docs/report-detail/06-results.md)에 있으며, 정상 팬 운전 336창은 [FG V3 관측](../docs/report-detail/05-fg-hardware.md)에 기록되어 있다.

| 구현 경로 | 입력·계산 조건 | 기록된 연결 정보 |
|---|---|---|
| FG V3 보고서 검증 사본 | 512표본, 창별 표본 시각, 1× 동기 진폭·위상 | 115200 baud, 정상 팬 336창 관측. 워크스페이스 원본 `D:\obsidian\claude\obsidian_export\Edge_module_folder\reports\algorithm-report-audit-2026-09-26\execution\firmware-build\` |
| 물리 근거 코어 | 512표본 X/Y/Z, 창별 실제 `fs`·FG 주파수, 정상 336창의 로그 중앙값/MAD | 신뢰 상한 `min(160 Hz, 0.4·fs)`, 가용 상태·후보 근거 계산 |
| 101특징 ML | 512표본 실시간 입력을 timestamp 기준 400점·400 Hz로 보간, 선택 축과 FG RPM 사용 | 101특징, 20×20 DFT 분해, 접은 선형 weight/bias, 230400 baud |
| CWRU 베어링 분류 | 4096표본, 12000 Hz, 6특징·네 클래스 | 호스트 재생에서 640창 특징·점수 비교, 40창 초기 이력 뒤 600창 4/5 평가 |

최신 물리·ML·CWRU 원본은 워크스페이스 경로 `D:\obsidian\claude\obsidian_export\Edge_module_folder\fault_evidence_core\`, `D:\obsidian\claude\obsidian_export\Edge_module_folder\firmware_ml_evidence_live\`, `D:\obsidian\claude\obsidian_export\Edge_module_folder\firmware_cwru_integration\`에 있다. 101특징 학습 원본은 `D:\obsidian\claude\obsidian_export\Edge_module_folder\fault_type_90_mechanical\`이다. 구현별 함수·수식·평가 분모는 [상세 해설](../docs/report-detail/README.md)과 [코드 대응표](../docs/report-detail/08-sources.md)에서 확인할 수 있다.

## 갱신 작업

- 공통 코어 수정 후 `bash sync_core.sh`로 ESP32 스케치 사본을 맞춘다.
- 대시보드 수정 후 `python tools/embed_dashboard.py`로 내장 자산을 만들고 `../docs/` 배포 사본을 갱신한다.
