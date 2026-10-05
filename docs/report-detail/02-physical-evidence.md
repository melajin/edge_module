# 2. 물리 근거 경로: 스펙트럼과 정상 대비 변화

**보고서 위치:** Ⅲ-2(7~9쪽), 표 4~6

## 쉬운 설명

물리 근거 경로는 먼저 파형에서 일정한 오프셋을 줄이고, 주파수별 진동 성분을 계산한다. 회전 속도를 알고 있으면 기본 회전 주파수 (1×)와 그 배수에 해당하는 대역을 정한다. 대역 에너지와 전체 진동 크기를 별도로 보면서 정상 기준과 비교한다. 여러 창에서 이어지는 근거는 시간 평활을 거쳐 보여 준다.

구현 대응은 세 경로로 나뉜다. 보고서의 중앙값/MAD 식은 `fault_evidence_core`의 robust baseline과 연결되고, 저장소 C V2는 로그 평균·표준편차, ML은 400점 PSD 정규화를 사용한다. 이어지는 절에서 각 계산을 기호와 코드 위치에 맞춰 설명한다.

## 공통 기호와 단위

| 기호 | 뜻 | 단위 |
|---|---|---|
| $N$ | 한 분석창 표본 수 | sample |
| $f_s$ | 표본 시간으로 구한 샘플링 주파수 | Hz |
| $\Delta f=f_s/N$ | FFT 빈 간격 | Hz/bin |
| $f_r$ | FG와 PPR로 정한 회전 주파수 | Hz(회전/s) |
| $P[k]$ | 3축 합산 단측 빈 전력 | 가속도² (입력이 g면 g²) |
| $E_B$ | 한 비중복 대역의 전력 합 | g²에 비례 |
| $R_B$ | 신뢰 대역 총 전력 중 한 대역의 비중 | % |
| $L_j=\ln(x_j)$ | 양수 특징의 자연로그 | 로그 특징 공간 |
| $m_j,s_j$ | 기준 로그 중앙값, robust scale | 로그 특징 공간 |
| $z_j,d_j$ | 표준화 이탈, 0~1 근거량 | 무차원 |

주파수 빈은 균일한 표본 시간축 $f_s$와 표본 수 $N$으로 정한다. 보고서 명목 조건 $f_s=400\,\mathrm{Hz}, N=512$의 창 길이는 약 1.28초, 빈 간격은 $400/512=0.78125\,\mathrm{Hz}$다. 첫 표본과 마지막 표본 사이에는 $(N-1)/f_s\approx1.2775\,\mathrm{s}$가 놓이고, 보고서의 1.28초 표기는 이를 반올림한 값이다.

## 보고서 수식이 가리키는 주파수 계산

주파수 분석 전 축별 평균을 뺀다. 이 단계는 일정한 센서 오프셋과 정적 중력 성분을 줄인다.

$$
x_a[n]=a[n]-\bar a,\qquad \bar a=\frac{1}{N}\sum_{n=0}^{N-1}a[n].
$$

여기서 $a[n]$은 한 축의 입력이며 가속도 단위는 g다. 주파수 빈은 $f_k=k\Delta f$에 놓인다. 보고서에서 의도한 에너지 설명은 각 축 빈 전력을 합쳐 대역에 더하는 방식이다.

$$
E_B=\sum_{k\in B}P[k],\qquad
R_B=100\frac{E_B}{E_{\rm trusted}}\;[\%].
$$

각 빈은 0.5×, 1×, 1.5×, 2×, 3× 대역 또는 상부/나머지 대역 가운데 한 곳에 배정한다. 신뢰 대역 안의 비중 합은 100%이며, 신뢰 범위와 양의 Nyquist 영역의 관계는 별도 관측 비율로 표시한다.

신뢰 대역 에너지 관측 비율(코드의 `trusted_coverage_percent`)은 신뢰 범위 안에서 계산된 전력이 양의 Nyquist 영역 전력에서 차지하는 비중이다.

$$
\mathrm{coverage}_{\rm energy}=100\frac{E_{\rm trusted}}{E_{\rm positive\;Nyquist}}\;[\%].
$$

회전 차수 대역의 중심은 $qf_r$, $q\in\{0.5,1,1.5,2,3\}$이다. 물리 코어의 신뢰 상한은 $f_{\max}=\min(\texttt{sensor\_max\_hz},0.4f_s)$이며 기본값은 $f_s=400\,\mathrm{Hz}$에서 160 Hz다. 각 대역 반폭은 $\max(2\Delta f,0.05qf_r)$이고 대역 경계·겹침 계산 결과를 `band_available[]`와 사유 코드에 담는다.

### 수치 예: 에너지 비중

설명용으로 한 대역의 전력 합이 $E_B=0.12\,\mathrm{g^2}$, 신뢰 대역 총합이 $E_{\rm trusted}=0.80\,\mathrm{g^2}$라고 놓자.

$$
R_B=100\times\frac{0.12}{0.80}=15\%.
$$

이 15%는 신뢰 대역 내부의 상대 분포다. 모든 대역 전력이 4배가 되는 크기 변화에서 $R_B$는 유지되고, $E_{\rm trusted}$와 시간영역 RMS는 절대 크기 변화를 나타낸다. 이 예의 전력 합은 $0.12\,\mathrm{g^2}$와 $0.80\,\mathrm{g^2}$로 둔 계산 입력이다.

## 현재 물리 근거 코어의 Hann·전력 정규화

로컬 `edge_module_c/report_physical/src/fault_evidence.c`는 보고서식 대역 계산을 확장한 별도 구현이다. $N=512$에서 **주기형 Hann**을 쓰고, $U=3/8$은 창 제곱의 평균 전력 보정량이다.

$$
w[n]=\frac12\left(1-\cos\frac{2\pi n}{N}\right),\qquad U=\frac38.
$$

각 축의 창 적용 표본을 $y_a[n]=(a_a[n]-\bar a_a)w[n]$라 하고, FFT는 소스의 부호 규약인 $X_a[k]=\sum_{n=0}^{N-1}y_a[n]\exp(-i2\pi kn/N)=\Re X_a[k]+i\Im X_a[k]$를 쓴다. 즉 지수부의 허수 부호는 음수다. DC는 제외하며 단측 평균제곱 전력을 축별로 계산해 더한다.

$$
P[k]=\sum_{a\in\{x,y,z\}}
\frac{c_k\left((\Re X_a[k])^2+(\Im X_a[k])^2\right)}{N^2U},
\quad
c_k=\begin{cases}1,&k=N/2\\2,&1\leq k<N/2.\end{cases}
$$

$P[k]$는 빈별 가속도 제곱 전력으로 단위는 g²다. 대역 에너지 $E_B$는 해당 빈 전력을 합산한다.

세 경로는 각각의 창과 정규화를 사용한다. 저장소 V2 `em_fft.c`는 $(N-1)$ 대칭 Hann, $4/N$ 진폭 보정, 진폭 제곱 대역 특징을 사용한다. 물리 코어는 512점 주기형 Hann과 $U=3/8$을, ML `psd()`는 400점 창과 $200^2$ 분모를 쓴다.

## 정상 기준: robust 중앙값/MAD 경로

정상으로 확인한 (336)개 유효 창에서 특징을 (x_{i,j}>0)로 두고 로그로 옮긴다.

$$
L_{i,j}=\ln(\max(x_{i,j},\epsilon)),\qquad
m_j=\operatorname{median}_i(L_{i,j}),
$$

$$
s_j=\max\left(1.4826\,\operatorname{median}_i|L_{i,j}-m_j|,\;0.20\right).
$$

`0.20`은 이 코어의 로그 MAD 최소 스케일 설정값이다. 보고서의 $s_{\min,j}$ 표기는 특징별 하한을 일반화한 식이고, 현재 이 코어의 기본값을 뜻한다.

보고서는 증가 방향의 양의 이탈을 간략히

$$
z^+_{t,j}=\max\left(0,\frac{\ln(x_{t,j})-m_j}{s_j}\right)
$$

로 나타낸다. 실제 `fault_evidence_core` 구현은 내부에서 부호가 있는 $z$를 만든 다음 0~1 근거량으로 바꾼다.

$$
z_{t,j}=\frac{\ln(x_{t,j})-m_j}{s_j},\qquad
d_{t,j}=\operatorname{clip}\left(\frac{z_{t,j}-2}{6-2},0,1\right).
$$

signed $z$는 계산 내부의 지역 변수이고 결과 구조에는 변환된 $d$를 `deviation[]`에 저장한다. $z\le2$ 구간은 $d=0$, $z\ge6$ 구간은 $d=1$이며, 두 경계 사이에서는 선형으로 0~1에 대응한다.

### 수치 예: 로그 변화가 근거량이 되는 과정

설명용 정상 기준을 $m=0.00$, MAD $=0.10$이라고 두자. 계산된 MAD 스케일 $1.4826(0.10)=0.14826$은 최소값 0.20보다 작으므로 $s=0.20$을 쓴다. 현재 양수 특징이 $x=e^{0.50}\approx1.649$이면,

$$
z=\frac{\ln(1.649)-0}{0.20}\approx2.50,
\qquad d=\frac{2.50-2}{4}=0.125.
$$

이 계산의 $d=0.125$는 설정된 증가 변환 구간에서 특징 변화량을 0~1로 나타낸 값이다. $(x,m,s)$는 로그 공간에서 비교되므로 곱셈형 크기 변화는 로그 차이로 표현된다.

## 후보 근거·점수·상대 비중

코어는 상관 특징을 그룹별로 묶고 각 그룹의 최대 근거값을 선택한다. $S$는 가용 에너지 특징 중 최대 $d$, $I$는 kurtosis·crest·RMS 변동 특징 중 최대 $d$, $P$는 품질 검증을 통과한 외부 물리 근거다.

$$
A=100\left[1-(1-S)(1-0.5I)(1-0.75P)\right].
$$

이 식은 에너지 이탈 $S$, 형상 변화 $I$, 외부 물리 근거 $P$를 0~100 bounded abnormality evidence로 결합한다. 예를 들어 $S=0.30,I=0.20,P=0$이면 $A=100[1-(0.70)(0.90)]=37$이다. 같은 예에서 $d_{1×}=0.25$, 1× 비중이 35%, 위상 coherence가 0.9일 때 불평형 후보 근거는

$$
C_{\rm imbalance}=A\sqrt{d_{1×}\,\operatorname{clip}(35/70,0,1)}\times0.9
\approx 37\sqrt{0.25\times0.5}\times0.9\approx11.8\%.
$$

이 예의 후보 강도는 11.8이며, 계산 입력은 $A=37$, $d_{1×}=0.25$, 비중 35%, coherence 0.9다. 불평형 근거 계산은 FG 동기 위상 이력 5회와 1× 대역 가용 상태를 입력 조건으로 사용한다.

### 시간 영역 형상 특징과 이완 후보

주파수 대역 계산에는 앞에서 정의한 Hann 창과 전력 정규화를 쓰고, 시간 영역의 radial 형상 특징은 축별 평균 제거 원표본으로 계산한다. 각 표본에서 3축 벡터 크기 제곱을

$$
r^2[n]=x_x^2[n]+x_y^2[n]+x_z^2[n],\qquad
\mu_2=\frac1N\sum_n r^2[n],\qquad
\mu_4=\frac1N\sum_n (r^2[n])^2
$$

라 두면 radial kurtosis는 $\kappa_r=\mu_4/\mu_2^2$이고 crest는 $C_r=\sqrt{\max_n r^2[n]/\mu_2}$다. 이 radial kurtosis 정의는 단일 축 정현파에서 1.5를 준다.

RMS 변동은 512점을 64점씩 8구간으로 나누어

$$
R_j=\sqrt{\frac1{64}\sum_{n=64j}^{64j+63}r^2[n]},\quad
\bar R=\frac18\sum_{j=0}^{7}R_j,\quad
CV_8=\frac{\sqrt{\frac18\sum_{j=0}^{7}(R_j-\bar R)^2}}{\bar R}
$$

로 계산한다. 분산은 8개 구간의 모집단 분산이며 $CV_8$은 원래 512점 창 안의 RMS 변화량을 요약한다. 기준 대비 이탈을 $d_{\kappa}$, $d_C$, $d_{CV}$라 하면 불규칙성 그룹은 $I=\max(d_{\kappa},d_C,d_{CV})$다.

이완 후보에는 회전 차수 $q\in\{0.5,1.5,2,3\}$ 중 가용한 차수만 쓴다. $d_q$는 각 대역 에너지 특징의 기준 대비 이탈이고 $R_q$는 신뢰 전력에서 해당 대역이 차지하는 비중[%]이다. 코드의 식은

$$
G=\max_{q\in Q_{\rm avail}}d_q,\qquad H=\sum_{q\in Q_{\rm avail}}R_q,\qquad
C_{\rm looseness}=A\sqrt{G\,\operatorname{clip}(H/50,0,1)\,I}.
$$

이다. $A$는 앞 절의 전체 이상 근거, $G$는 회전 차수 대역 증가, $H$는 합산 비중, $I$는 시간 형상 변화다. 이완 후보의 strength는 세 단서를 결합한 0~100 근거 강도이며, 대역 상태는 `band_available[]`에 저장된다.

각 가용 후보의 **근거 강도(`strength_percent`)**는 후보별 0~100 지표다. `relative_percent`는 `candidate_available[]`로 가용한 후보 강도의 합을 분모로 계산한 상대 비중이다.

$$
R_c=100\frac{C_c}{\sum_{q\in\mathrm{available}}C_q}\;[\%].
$$

현재 코어는 $A\ge20$, 후보 강도 합계 $\ge20$, 연속 입력 3창 이상과 모든 가용 후보별 3회 관측을 충족하면 상대 비중을 산출한다. 이 상태 전에는 `relative_percent=null`을 내보내며 분모에는 `candidate_available[]`로 가용한 후보가 들어간다.

## 시간 이력과 입력 상태

연속 입력 평활은 $y_t=y_{t-1}+0.4(x_t-y_{t-1})$의 EWMA로 계산한다. 창 간 시간 순서·간격, baseline generation, 설정 세대, 입력 품질을 이력 상태에 반영하며 500 ms 초과 간격과 새 baseline/config 세대에서 이력을 초기화한다.

| 입력 상태 | 결과 상태·필드 | 계산 단계 |
|---|---|---|
| 분석창 오류 또는 센서 범위 초과 | `error_code`, 계산 상태 `invalid` | 특징 산출 상태에 입력 오류 사유를 기록 |
| 회전 차수 대역 신뢰 범위 | `band_available[]`, 이유 코드 | 차수별 대역 관측 상태를 후보에 연결 |
| 1× 대역과 FG 위상 이력 5회 | 불평형 후보 계산 입력 | 1× 이탈·비중·coherence로 후보 강도 계산 |
| 커플링·방향·베어링 기하·벨트 정보 | `candidate_available[]`의 후보별 상태 | 설비정보 입력이 후보 계산 경로를 정함 |
| 정상 기준과 설정 세대 | `baseline_status`: `ok`, `baseline_not_ready`, `config_changed` | baseline generation 상태를 현재 입력과 대조 |
| 3창 연속 이력, 모든 가용 후보별 3회 관측 | `relative_percent` 수치 또는 `null` | 모든 가용 후보가 조건을 채우면 전체 가용 후보 강도로 비중 계산 |

## 기존 저장소 V2와 수식 간 차이

| 구현 | 전처리·특징 | 정상 통계와 판정 | 문서 연결 |
|---|---|---|---|
| `edge_module/edge_module_c/core/`의 기존 V2 | 평균 제거→$(N-1)$ 대칭 Hann→진폭 스펙트럼; RMS, H1/H2/H3 비율, HF 비율 | 특징을 $\ln(x+10^{-6})$로 변환해 평균과 표본 표준편차 저장. 기본 $|z|>3$, 특징 수 조건, 기본 4/5 확인. | PDF [9]의 코드 경로. [`em_detector.c`](../../edge_module_c/core/em_detector.c), [`em_fft.c`](../../edge_module_c/core/em_fft.c) |
| 보고서 수식/`fault_evidence_core` | 512점 3축 합산 주기형 Hann 전력, 7개 비중복 대역·형상 특징 | 로그 중앙값/MAD, 내부 z→d, 후보 근거 강도·EWMA | 현재 위치: `edge_module_c/report_physical/src/fault_evidence.c` |
| 보고서 FG V3 검증 사본 | FG 동기 사인·코사인 투영으로 1× 진폭·위상 | 최근 다섯 유효창의 위상 집중도와 4/5 상태정책 | `edge_module_c/esp32/edge_alimi_adxl345_fg_report/source_snapshot/` (현재 저장소 바깥) |
| 최신 확장 실험 | 최신 `edge_module_c/report_ml/src/v3_signal.c`는 1× 외 2×/3× 진폭과 비율을 계산 | 물리 근거와 ML 계층을 연결한 별도 실험 경로 | 현재 위치: `edge_module_c/report_ml/` |

현재 저장소 C V2 기본값은 $N=1024$, $f_s=1000\,\mathrm{Hz}$이고, Python V2 설명은 MPU-6050·1 kHz를 사용한다. PDF 표 4는 512표본·400 Hz 조건을 제시한다. 이 세 구현 조건을 버전별 설정으로 정리한다.

## 근거 파일과 확인 범위

- 저장소 내 기존 구현: [`em_fft.c`](../../edge_module_c/core/em_fft.c), [`em_features.c`](../../edge_module_c/core/em_features.c), [`em_detector.c`](../../edge_module_c/core/em_detector.c), [`em_config.h`](../../edge_module_c/core/em_config.h), [`feature_extraction_v2.py`](../../src/feature_extraction_v2.py).
- 보고서 robust baseline 계산: 현재 위치 `edge_module_c/report_physical/src/fault_evidence.c`의 `fec_extract()`, `fec_baseline_finish()`, `fec_evaluate()`; 개요: `edge_module_c/report_physical/README.md`.
- FG V3: 현재 위치 `edge_module_c/esp32/edge_alimi_adxl345_fg_report/source_snapshot/v3_signal.c`의 `v3_analyze_1x()`와 `em_v3.c`의 `em_v3_update()`.
- 실행 기록에는 Python/C 정책 비교 219회, 합성 C 신호 검사, ESP32 컴파일 결과가 있다. 이 기록은 소프트웨어 비교·합성 입력·빌드의 세 유형으로 구분된다.
