# 보고서 101특징 Python 정의

`a_features.py`는 선택 축 400점·400 Hz 입력의 101특징을 정의한다. `a_group_develop.py`는 trial-group 단위 개발 분할과 모델 평가 경로이며, 생성 C 계수와 특징 계산은 [report_ml/](../../edge_module_c/report_ml/)에 연결한다.

개발 자료는 Mendeley Data Mechanical faults Version 3이며 DOI는 `10.17632/zx8pfhdtnb.3`이다. 특징 이름·순서·스펙트럼·포락선·AR24와 fold 마진 계산은 [보고서 모델 해설](../../docs/report-detail/03-ml-model.md)에서 상세히 읽을 수 있다. 원 파형·분할 모델 입력은 출처 계약과 개발 도구의 인자로 지정하며, 생성 헤더는 해당 출처와 함께 보관한다.
