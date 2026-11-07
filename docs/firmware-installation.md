# ESP32 펌웨어 설치 준비

이번 산출물은 `postreport_v2` 소스와 ESP32 설치 파일이다. 사용자의 범위에 따라 장치를 연결하거나 업로드·실제 운전 검증을 수행하지 않는다. 코드 검사, 호스트 모의시험, ESP32 빌드와 실제 장치 실험은 별개로 기록한다.

## 패키지 만들기

대상 프로젝트는 `edge_module_c/esp32/edge_alimi_adxl345_fg_report/`다. PlatformIO와 호스트 테스트용 GCC·Python을 준비한 뒤 이 디렉터리에서 실행한다.

```powershell
python tools/run_host_checks.py
python tools/package_firmware.py --pio pio
```

`pio`가 PATH에 없다면 `--pio`에 PlatformIO 실행 파일의 절대 경로를 지정한다. 패키징은 실제 빌드를 실행하고 출력은 기본 `dist/`에 만든다. `--output <디렉터리>`로 저장 위치를 바꿀 수 있다. release 패키지는 Git 변경이 없는 커밋을 기준으로 만들며, `--allow-dirty`로 만든 개발용 산출물은 release로 표시하지 않는다.

```powershell
python tools/package_firmware.py --verify <생성한.zip>
```

ZIP에는 설치 이미지와 `manifest.json`, 설치 안내를 포함한다. manifest는 소스 커밋·변경 여부, `esp32dev` 보드, platform/framework/toolchain 버전, 빌드 설정, flash 주소·크기와 파일별 SHA-256을 기록한다. flash 주소와 옵션은 설치된 PlatformIO 빌드 환경에서 수집한다. 파일 이름만 같은 다른 빌드의 이미지를 섞어 쓰지 않는다.

동일 입력 파일로 ZIP을 다시 구성하는 재현성과 다른 컴퓨터에서 펌웨어를 컴파일했을 때 바이트까지 동일한지는 서로 다르다. 패키지 검사에 통과했다고 컴파일의 완전한 재현성을 검증한 것으로 표현하지 않는다.

## 실제 업로드는 다음 단계

대상은 일반 ESP32용 `esp32dev`다. ESP32-S2/S3/C3 등 다른 칩이나 다른 partition 설정에 그대로 쓰지 않는다. 실제 업로드 때는 아래 순서로 확인한다.

1. 정확한 보드·칩·flash 크기·포트를 확인한다. Bluetooth COM 포트나 과거 기록의 포트를 추측해서 선택하지 않는다.
2. 기존 장치의 배선, 전원과 모터 출력 상태를 확인한다. 리셋이나 기존 펌웨어 정지의 영향도 확인한다. 이 문서만으로 미확인 전압·FG 출력을 ESP32에 직접 연결하지 않는다.
3. flash 전체를 읽어 백업하고 파일 크기·해시를 기록한다. 기존 partition과 NVS 배치를 확인한 뒤 패키지와 비교한다.
4. ZIP 내부 안내의 명령에서 명시적으로 확인한 포트를 사용한다. 해당 명령은 패키지 생성 중 자동 실행되지 않는다. 전체 지우기(`erase_flash`)는 사용하지 않는다.
5. 업로드 후 부팅 이벤트와 `profile`을 확인한다. 기존 기준을 사용할 경우 `import <조건 ID>` → 후보 확인 → `approve <후보 ID>` 절차를 따른다. 저장 보류가 있으면 원인을 확인하고 `recover` 결과를 점검한다.

새 코드는 구형 `edgev3/profile`을 읽기 전용으로 다루지만, 설치 이미지가 기존 partition과 충돌하지 않는다는 보장은 아니다. 기존 flash 백업과 배치 비교를 생략하지 않는다. 패키지에는 자동 포트 선택·업로드·모터 운전 기능이 없다.

## 부팅 후 확인할 의미

부팅은 `paused=true`다. `start`는 센서 측정을 시작하며 모터를 돌리지 않는다. `stop` 역시 측정만 멈춘다. 센서 주소·핀·표본 설정은 [펌웨어 README](../edge_module_c/esp32/edge_alimi_adxl345_fg_report/README.md), 운영 명령은 [정상 기준 관리](baseline-lifecycle.md)에 설명한다.

향후 장치 시험에서는 보드·센서·전원·부착·PPR·회전수·부하 조건과 펌웨어 해시를 먼저 기록한다. 이어 예상 결과, 실제 로그, 측정값, 해석을 남긴다. 센서 오류, FG 오류, 저장 보류, 수신 누락과 모델 판정은 서로 다른 현상이다. 장기 운전·전원 차단·실제 고장 성능은 해당 조건의 실험이 끝난 뒤에만 검증했다고 보고한다.
