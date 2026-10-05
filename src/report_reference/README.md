# 보고서 Python 기준

보고서 FG C 정책과 공개 파형 계산을 대조하는 Python 기준 패키지를 담는다. 원본 패키지의 계산 순서와 입력 계약을 보존하고, C 어댑터·모델·정책 비교 도구에서 저장소 내부 경로로 불러온다.

FG 3축 512표본·시각·PPR 입력은 [보고서 펌웨어](../../edge_module_c/esp32/edge_alimi_adxl345_fg_report/), 101특징은 [Python 모델 정의](../../ml/report_model/), CWRU 12 kHz·4096점은 [베어링 기준](../../edge_module_c/report_cwru/)에 각각 연결한다. 검사 결과에는 정책 갱신 횟수와 입력 출처를 기록한다.
