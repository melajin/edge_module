# 3. 학습 모델: 파형을 101개 값과 네 후보 점수로 바꾸기

**보고서 위치:** Ⅲ-3(10~11쪽), 표 7, 표 1(3~4쪽)의 분류 개발 성적

## 쉬운 설명

모델 경로는 센서 파형과 회전 속도를 101개 수치로 바꾸고, 학습 단계에서 저장한 표준화·선형 분류 기준으로 네 상태 마진을 계산한다. 가장 큰 마진의 클래스를 `label` 후보로 출력한다.

개발 평가는 20개 trial-group을 나눈 5개 fold의 OOF 결과이며, ESP32 추론 자산은 기존 다섯 fold 모델의 평균 마진식이다. provenance에는 `ensemble_evaluated=false`, `live_validated=false`가 기록돼 있다.

## 보고서 모델의 입력 조건과 학습 자료

| 항목 | 조건 | 확인 범위 |
|---|---|---|
| 학습 공개 자료 | Mendeley Data v3, *Mechanical faults in rotating machinery dataset (normal, unbalance, misalignment, looseness)*; 정상·불평형·축 정렬 불량·기계적 이완, 총 20회 시험(각 상태 5회) | 공식 Mendeley 페이지는 Version 3, 2023-07-28, DOI `10.17632/zx8pfhdtnb.3`, CC BY 4.0로 표기한다. 제공처의 벤치 설명은 채널 4개, 25 kHz, 파일당 25,000표본, 약 1초 및 약 1,238 RPM을 명시한다. |
| 개발 분할 | 20개 trial-group을 나눈 5개 fold, 각 fold에서 네 상태의 시험 회차 하나씩 | 1,200 OOF 기록, 상태별 300개, 보고 정확도 93.67% |
| 선택된 대표 모델 | `limited400`, `single_ch2`, `extended`, `linear_c1` | 2026-10-02 기존 개발 fold의 5개 선형 모델 export 기록 |
| 학습 파형 창 | 400 Hz, 400 표본, 약 1초; 공개 자료의 명목 회전 속도 | `limited400` 프로파일과 provenance 상태 `raw_reopened=false` |
| 단일 입력 축 | `single_ch2`; 개발 기록은 디스크 측 수직 센서로 설명 | 실시간 어댑터는 사용자가 선택한 ADXL345 한 축을 입력으로 사용 |
| 학습 샘플링 변환 | 공개 25 kHz 파형을 정보 제한 profile로 변환 | 개발 profile의 `resample_poly`와 실시간 512개 시각→400점 선형 보간 절차 |

Mendeley v3 manifest에는 데이터 버전, CC BY 4.0 라이선스, trial 파일과 공개 파일 ID·메타데이터가 기록돼 있다. `feature_contract`는 profile, 입력 축, 특징 순서를 정하고, 실시간 ADXL345 입력은 g 단위로 변환한다. export provenance에는 원 파형 처리 상태 `raw_reopened=false`가 저장돼 있다. [출처표](08-sources.md)는 공식 공개 페이지와 현재 계산 자산을 연결한다.

## 101개 특징의 계산 순서와 정의

특징 이름과 순서는 선택 모델의 `feature_contract.json` 및 `model_provenance.json`에 배열로 고정돼 있다. C 추출기도 아래 순서로 값을 채운 뒤 `n==101`인지 검사한다. 모든 `log_...` 변환은 자연로그이며 $\ln(\max(v,10^{-20}))$를 쓴다. `log_`가 없는 값은 해당 feature 정의에 있는 그대로 두고, 그 다음 모델 표준화로 넘어간다.

### 1~10: 시간·기본 스펙트럼 특징

입력 400점 $s[n]$에서 평균을 빼 $x[n]$을 만든다. 여기서 `rms`, peak, 평균 절대값 등은 모두 이 centered input으로 계산한다.

| 인덱스 | 정확한 이름 | 정의 | 저장 변환 |
|---:|---|---|---|
| 1 | `log_rms` | $\sqrt{N^{-1}\sum x[n]^2}$ | 자연로그 |
| 2 | `log_h1` | 전체 PSD 대비 $f_r\pm7\,\mathrm{Hz}$ 대역 에너지 비율 | 자연로그 |
| 3 | `log_h2` | 전체 PSD 대비 $2f_r\pm7\,\mathrm{Hz}$ 비율 | 자연로그 |
| 4 | `log_h3` | 전체 PSD 대비 $3f_r\pm7\,\mathrm{Hz}$ 비율 | 자연로그 |
| 5 | `log_hf_fraction` | $4.5f_r$부터 200 Hz까지의 에너지/전체 에너지 | 자연로그 |
| 6 | `log_crest` | $\max|x|/\mathrm{RMS}(x)$ | 자연로그 |
| 7 | `log_kurtosis` | $E[x^4]/E[x^2]^2$ | 자연로그 |
| 8 | `log_impulse` | $\max|x|/E|x|$ | 자연로그 |
| 9 | `log_shape` | $\mathrm{RMS}(x)/E|x|$ | 자연로그 |
| 10 | `skewness` | $E[x^3]/\mathrm{RMS}(x)^3$ | 로그 없이 사용 |

여기서 $f_r=\mathrm{RPM}/60$이며 400 Hz 프로파일의 201개 빈은 0~200 Hz, 간격 1 Hz다. 이 경로의 주파수 대역 적분은 ML 코드의 `energy()` 정의를 따른다.

### 11~46: 회전 차수별 에너지 쌍

18개 회전 차수 $q=0.5,1.0,1.5,\ldots,9.0$ 각각에 대해 두 특징을 만든다.

$$
E_q=\sum_{k:\,(q-0.15)f_r\le kf_s/N < (q+0.15)f_r}P[k],\qquad
E_{\rm all}=\sum_{k=0}^{200}P[k].
$$

`log_order_q = ln(E_q/E_all)`은 전체 대비 비율, `log_order_abs_q = ln(E_q)`는 절대 대역 에너지의 자연로그다. $q$는 속도에 따른 회전 차수이고 $E_q$의 주파수 폭은 $0.30f_r$다. 숫자는 feature 이름을 그대로 적는다.

| 인덱스 | 차수 (q) | 먼저 저장하는 비율 특징 | 바로 뒤 절대 에너지 특징 |
|---:|---:|---|---|
| 11~12 | 0.5 | 11 `log_order_0.5` | 12 `log_order_abs_0.5` |
| 13~14 | 1.0 | 13 `log_order_1.0` | 14 `log_order_abs_1.0` |
| 15~16 | 1.5 | 15 `log_order_1.5` | 16 `log_order_abs_1.5` |
| 17~18 | 2.0 | 17 `log_order_2.0` | 18 `log_order_abs_2.0` |
| 19~20 | 2.5 | 19 `log_order_2.5` | 20 `log_order_abs_2.5` |
| 21~22 | 3.0 | 21 `log_order_3.0` | 22 `log_order_abs_3.0` |
| 23~24 | 3.5 | 23 `log_order_3.5` | 24 `log_order_abs_3.5` |
| 25~26 | 4.0 | 25 `log_order_4.0` | 26 `log_order_abs_4.0` |
| 27~28 | 4.5 | 27 `log_order_4.5` | 28 `log_order_abs_4.5` |
| 29~30 | 5.0 | 29 `log_order_5.0` | 30 `log_order_abs_5.0` |
| 31~32 | 5.5 | 31 `log_order_5.5` | 32 `log_order_abs_5.5` |
| 33~34 | 6.0 | 33 `log_order_6.0` | 34 `log_order_abs_6.0` |
| 35~36 | 6.5 | 35 `log_order_6.5` | 36 `log_order_abs_6.5` |
| 37~38 | 7.0 | 37 `log_order_7.0` | 38 `log_order_abs_7.0` |
| 39~40 | 7.5 | 39 `log_order_7.5` | 40 `log_order_abs_7.5` |
| 41~42 | 8.0 | 41 `log_order_8.0` | 42 `log_order_abs_8.0` |
| 43~44 | 8.5 | 43 `log_order_8.5` | 44 `log_order_abs_8.5` |
| 45~46 | 9.0 | 45 `log_order_9.0` | 46 `log_order_abs_9.0` |

`E_q`는 1 Hz 빈의 아래쪽 경계를 포함하고 위쪽 경계를 제외한다. 400점 입력의 주파수 범위는 0~200 Hz이며 `order_coverage_mask`와 `unavailable_orders`가 차수 대역의 가용 상태를 표시한다.

### 47~66: 10 Hz 대역 분포

각 특징은 $[10i,10(i+1))\,\mathrm{Hz}$ 대역 에너지 비율 $E_i/E_{\rm all}$의 자연로그다.

| 인덱스 | 이름 | 정의 |
|---:|---|---|
| 47 | `log_hz_band_0_10` | 0~10 Hz 구간 |
| 48 | `log_hz_band_10_20` | 10~20 Hz 구간 |
| 49 | `log_hz_band_20_30` | 20~30 Hz 구간 |
| 50 | `log_hz_band_30_40` | 30~40 Hz 구간 |
| 51 | `log_hz_band_40_50` | 40~50 Hz 구간 |
| 52 | `log_hz_band_50_60` | 50~60 Hz 구간 |
| 53 | `log_hz_band_60_70` | 60~70 Hz 구간 |
| 54 | `log_hz_band_70_80` | 70~80 Hz 구간 |
| 55 | `log_hz_band_80_90` | 80~90 Hz 구간 |
| 56 | `log_hz_band_90_100` | 90~100 Hz 구간 |
| 57 | `log_hz_band_100_110` | 100~110 Hz 구간 |
| 58 | `log_hz_band_110_120` | 110~120 Hz 구간 |
| 59 | `log_hz_band_120_130` | 120~130 Hz 구간 |
| 60 | `log_hz_band_130_140` | 130~140 Hz 구간 |
| 61 | `log_hz_band_140_150` | 140~150 Hz 구간 |
| 62 | `log_hz_band_150_160` | 150~160 Hz 구간 |
| 63 | `log_hz_band_160_170` | 160~170 Hz 구간 |
| 64 | `log_hz_band_170_180` | 170~180 Hz 구간 |
| 65 | `log_hz_band_180_190` | 180~190 Hz 구간 |
| 66 | `log_hz_band_190_200` | 190~200 Hz 구간 |

### 67~101: 스펙트럼 모양, 변동, envelope, AR

| 인덱스 | 정확한 이름/순서 | 정의 | 저장 변환 |
|---:|---|---|---|
| 67 | `spectral_entropy` | $q_k=P[k]/E_{\rm all}$, $H=-\sum_{k=0}^{200}q_k\ln(q_k+\epsilon)/\ln(201)$ | 0~1로 정규화한 엔트로피 값 |
| 68 | `log_spectral_flatness` | 201개 빈의 기하평균/산술평균 비율 | 그 비율의 자연로그 |
| 69 | `rms_cv` | 400점을 40점씩 10조각으로 나눠 각 조각 RMS의 표준편차/평균 | 로그 없이 사용 |
| 70 | `envelope_cv` | DFT 기반 Hilbert 해석 신호 크기(envelope) 400점의 모집단 표준편차/평균 | 로그 없이 사용 |
| 71 | `log_envelope_order_0.5` | envelope PSD의 0.5× 대역 에너지/전체 envelope PSD | 자연로그 |
| 72 | `log_envelope_order_1.0` | envelope PSD의 1× 대역 에너지/전체 | 자연로그 |
| 73 | `log_envelope_order_1.5` | envelope PSD의 1.5× 대역 에너지/전체 | 자연로그 |
| 74 | `log_envelope_order_2.0` | envelope PSD의 2× 대역 에너지/전체 | 자연로그 |
| 75 | `log_envelope_order_2.5` | envelope PSD의 2.5× 대역 에너지/전체 | 자연로그 |
| 76 | `log_envelope_order_3.0` | envelope PSD의 3× 대역 에너지/전체 | 자연로그 |
| 77~100 | 순서대로 `ar24_01`, `ar24_02`, …, `ar24_24` | 평균 제거 파형의 24차 AR 계수 $a_1\ldots a_{24}$. biased autocorrelation Toeplitz 행렬을 풀며 대각에 $10^{-6}\operatorname{var}(x)$ ridge를 더한다. | 로그 없이 사용 |
| 101 | `log_ar24_residual_fraction` | $\ln(\max((R_0-\sum_{j=1}^{24}a_jR_j)/\operatorname{var}(x),10^{-20}))$ | residual fraction의 자연로그 |

### Hilbert envelope와 변동계수

400점 평균 제거 파형의 DFT를 $X[k]$라 할 때 코드의 analytic-signal 마스크는 DC와 Nyquist를 그대로 두고, 양의 주파수는 두 배로 하고, 음의 주파수는 0으로 만든다.

$$
H[k]=\begin{cases}
1,&k=0\text{ 또는 }k=200,\\
2,&1\le k\le199,\\
0,&201\le k\le399.
\end{cases}
\qquad
z[n]=\frac1{400}\sum_{k=0}^{399}H[k]X[k]e^{i2\pi kn/400},\quad
e[n]=|z[n]|.
$$

Feature 70은 envelope 400점의 모집단 CV를 저장하고, 71~76은 envelope Hann 기반 PSD의 차수 에너지 비율을 저장한다.

### AR(24) 계수와 잔차 특징

AR 계산의 입력은 평균 제거 파형 $x[n]$이다. 지연 $\ell=0,\ldots,24$에 대한 biased autocorrelation은 가능한 곱의 합을 항상 전체 $N=400$으로 나눈다.

$$
R_\ell=\frac1N\sum_{n=0}^{N-\ell-1}x[n]x[n+\ell].
$$

계수 $a_1,\ldots,a_{24}$는 ridge가 추가된 Toeplitz 방정식

$$
\sum_{j=1}^{24}\left(R_{|i-j|}+\lambda\,\mathbf{1}_{i=j}\right)a_j=R_i,
\quad i=1,\ldots,24,\qquad
\lambda=10^{-6}\operatorname{var}(x)
$$

77~100번 위치에는 $a_1$~$a_{24}$ 계수를 순서대로 저장한다.

$$
E_{24}=R_0-\sum_{j=1}^{24}a_jR_j,\qquad
f_{101}=\ln\left(\max\left(\frac{E_{24}}{\operatorname{var}(x)},10^{-20}\right)\right).
$$

예를 들어 분산 R0=var(x)=1이고 예측 항의 합이 0.6이면 잔차 비율은 0.4, 저장값은 ln(0.4)≈-0.916이다. Feature 101은 이 로그 잔차 비율이며, 77~100번 계수와 함께 400점 파형의 자기상관 구조를 표현한다.

## PSD와 Hilbert envelope는 ML 전용 정규화다

ML 경로는 400점 입력과 별도 `ML_HANN[400]` 표를 사용한다. 이 표는 $0.5(1-\cos(2\pi n/400))$로 생성된다. 즉 동일 계열의 주기형 Hann이지만 512점 물리 코어 창과 표본 수·빈 간격이 다르고, 전력 보정도 다르다.

$$
P_{\rm ML}[k]=\frac{\bigl(\Re X[k]\bigr)^2+\bigl(\Im X[k]\bigr)^2}{200^2}\times
\begin{cases}1,&k=0\text{ 또는 }k=200\\2,&0<k<200.\end{cases}
$$

코드에서 N=400, fs=400 Hz로 둬 빈 간격은 1 Hz다. ML PSD는 200² 기준 정규화를 사용하고, 물리 코어는 창 전력 보정 U=3/8을 적용한다. 에너지 비율 특징은 각 경로의 E_all로 나누며, `log_order_abs`는 원 에너지 합의 로그를 저장한다.

### 수치 예: 회전 차수와 Nyquist

공개 자료의 약 $1238\,\mathrm{RPM}$을 쓰면 $f_r=1238/60\approx20.63\,\mathrm{Hz}$이고 $1\,\mathrm{Hz}$ 빈 간격에서 $0.5×\approx10.32\,\mathrm{Hz}$, $3×\approx61.90\,\mathrm{Hz}$, $9×\approx185.70\,\mathrm{Hz}$가 된다. 이를 기계 기준 주파수의 차수로 묶는 과정이 `log_order_*`다.

현재 정상 팬의 1× 약 68 Hz를 400 Hz ML 입력에 대입하면 3× 중심은 약 204 Hz이고 Nyquist는 200 Hz다. 101개 특징과 함께 `order_coverage_mask`, `unavailable_orders`가 대역 상태를 출력하며 마스크 `false`는 해당 대역 빈의 가용성 상태를 나타낸다.

## 학습 표준화, 다섯 fold와 후보 선택

학습된 표준화 평균·척도를 fold $k$, 특징 $j$에 대해 $\mu_{kj},\sigma_{kj}$, 클래스 $c$의 선형 계수·절편을 $w_{kcj},b_{kc}$라 하자.

$$
u_{kj}=\frac{x_j-\mu_{kj}}{\sigma_{kj}},\qquad
m_{kc}=b_{kc}+\sum_{j=1}^{101}w_{kcj}u_{kj},
$$

$$
M_c=\frac{1}{5}\sum_{k=0}^{4}m_{kc},\qquad
\widehat c=\operatorname*{argmax}_{c\in\{\text{imbalance, mechanical\_looseness, misalignment, normal}\}}M_c.
$$

현재 C 생성 코드는 표준화 평균과 fold별 선형 계수를 평균 마진식에 대수적으로 접어 `ML_ENSEMBLE_WEIGHT`와 `ML_ENSEMBLE_BIAS`에 저장한다. `ml_reference_predict()`는 101곱-합으로 클래스별 마진을 계산한다. 점수 유형은 LinearSVC decision margin이고 출력은 네 클래스 마진으로 구성된다.

계산 예의 점수 벡터 [0.14,0.18,-0.02,0.06]에서는 최댓값 0.18인 mechanical_looseness가 선택된다. 벡터 값은 클래스별 마진 예시다.

## 개발 결과와 실시간 입력 상태

| 근거 | 저장 기록 | 상태 정보 |
|---|---|---|
| 보고서 개발 CV | 20개 trial-group을 나눈 5개 fold, 1,200 OOF 기록, 정확도 93.67%; 축 정렬 재현율 86.00%, 이완 재현율 88.67% | 지정 공개 자료·profile·fold 기준의 개발 평가 |
| 다섯 모델 provenance | five `fold*.joblib` SHA, `training: existing development CV folds; no refit` | C export에 사용한 모델 자산과 lineage |
| 평균 마진 앙상블 | `ensemble_evaluated=false`, `live_validated=false` | 현재 export의 평가 상태 필드 |
| 실시간 입력 | 512 표본, 380~420 Hz, 시간축 선형 보간 후 400점, FG RPM, 단일 선택 축 | `ml_live.c` 입력 변환과 `experimental_domain_mismatch` 상태 필드 |
| 입력 품질 | NaN, 센서 포화, 창 오류, FG 품질 사유와 `label`, `scores`, order mask | 품질 오류와 대역 가용 상태를 출력 필드로 기록 |
| C 구현 비교 | 공개 파형 16개와 합성 10개 특징·마진 비교, 1,200 저장 입력 마진 비교, ESP32 target build | 호스트 비교·합성 입력·타깃 빌드 기록 |

보고서 11쪽의 베어링 분류는 CWRU 별도 경로이며 12 kHz, 4,096표본, 6특징과 네 클래스 점수를 사용한다. 400점·101특징 선형 앙상블은 다른 분석 경로다.

## 별도 CWRU 경로: 6특징·12 kHz 베어링 분류

CWRU 구현은 ratedRPM을 기준으로 고대역 단일채널 파형을 분석한다. `EM_CWRU_N=4096`, `fs=12000 Hz`에서 `em_cwru_features()`가 시간 RMS·초과 첨도·1×/2×/3× 및 고주파 진폭합 비율의 6개 값을 만든다. 물리 포락선 후보는 별도 경로에서 고대역 진동 envelope, 베어링 기하와 회전 주파수를 조합한다. 400점 ML 특징과 물리 코어의 제곱 전력 대역은 각각 별도 계산 계약이다.

### 입력·회전 기준과 진폭 스펙트럼

입력의 평균을 뺀 원표본 $x[n]=s[n]-\bar s$의 FFT를 사용한다.

$$
X[k]=\sum_{n=0}^{4095}x[n]e^{-i2\pi kn/4096},\qquad
A[k]=\frac{2}{4096}|X[k]|,\qquad f_k=k\frac{12000}{4096}.
$$

각 양의 주파수 빈의 크기 $A[k]$는 가속도와 같은 단위다. 코드는 Nyquist보다 낮은 $k=0,\ldots,2047$을 보며, 총 진폭합 분모에는 $f_k>1$ Hz인 빈만 더하고 작은 바닥값 $10^{-12}$를 둔다.

탐색 범위에 포함된 FFT 빈의 진폭 최대 위치를 $f_r$로 사용하고, 빈 탐색 상태는 정격 회전 주파수 $f_{r,0}$과 함께 관리한다.

$$
B=\max\left(5\,\mathrm{Hz},\,2\frac{f_s}{N}\right)
=\max(5,5.859375)\,\mathrm{Hz}=5.859375\,\mathrm{Hz}.
$$

예를 들어 정격 1800 RPM이면 탐색 범위는 24~36 Hz다. 진폭 최대 빈이 k=10이고 fr=29.296875 Hz라면 1× 대역은 23.4375~35.15625 Hz다. 2×와 3× 대역도 같은 fr와 B를 중심으로 계산한다.

### 여섯 특징의 실제 정의

총 진폭합을 $T=10^{-12}+\sum_{k:f_k>1\,\mathrm{Hz}}A[k]$로 둔다. 특징은 다음 순서다.

| 인덱스 | 특징 | 코드 계산 |
|---:|---|---|
| 0 | AC RMS | $\sqrt{\frac1N\sum_{n=0}^{N-1}x[n]^2}$ |
| 1 | 초과 첨도(excess kurtosis) | $\frac{\frac1N\sum (x[n]-\bar x)^4}{\left(\frac1N\sum (x[n]-\bar x)^2\right)^2}-3$ |
| 2 | 1× 진폭합 비율 | $\frac{\sum_{k:|f_k-f_r|\le B}A[k]}{T}$ |
| 3 | 2× 진폭합 비율 | $\frac{\sum_{k:|f_k-2f_r|\le B}A[k]}{T}$ |
| 4 | 3× 진폭합 비율 | $\frac{\sum_{k:|f_k-3f_r|\le B}A[k]}{T}$ |
| 5 | 고주파 진폭합 비율 | $\frac{\sum_{k:10f_r\le f_k\le0.8(f_s/2)}A[k]}{T}$ |

주파수 경계는 코드처럼 양끝을 포함한다. 고주파 상한은 0.8 Nyquist, 즉 4,800 Hz다. RMS는 시간영역 AC 크기, 첨도는 펄스성 형상, 차수·고주파 특징은 진폭합 비율로 계산한다.

앞의 계산 예에서 분모 T=12인 진폭 합이 1× 2.4, 2× 1.2, 3× 0.6, 고주파 3.0이면 해당 비율은 각각 0.20, 0.10, 0.05, 0.25다. 나머지 진폭은 총합 분모에 포함된다.

### 표준화·4상태 결정·4/5 확인

저장된 StandardScaler 평균 $\mu_i$와 척도 $\sigma_i$로

$$
z_i=\frac{F_i-\mu_i}{\sigma_i},\qquad
D_c=b_c+\sum_{i=0}^{5}w_{ci}z_i,\qquad
\widehat c=\operatorname*{argmax}_{c\in\{0,1,2,3\}}D_c.
$$

를 계산한다. 클래스 인덱스는 0=ball, 1=inner race, 2=normal, 3=outer race다. C는 네 클래스 선형 점수의 argmax를 반환한다. 점수 예 [-0.6,-0.2,1.1,-0.4]에서는 normal(인덱스 2)이 선택된다.

시간 확인은 파일마다 초기화한다. 각 창의 비정상 표시를 $u_t=1[\widehat c_t\ne2]$로 두고 최근 다섯 창의 합을 계산한다.

$$
\mathrm{confirmed}_t=1\left[\sum_{j=t-4}^{t}u_j\ge4\right]
$$

최근 다섯 창의 비정상 표시가 4회 이상이면 상태를 confirmed로 출력한다. Argmax는 네 클래스 후보를 선택하고 4/5 투표는 정상/비정상 상태를 갱신한다.

### 보고서 창 수와 재생 결과 읽기

CWRU 잠금 시험은 10개 파일에서 640 분석창을 구성했다. Python/C 특징·점수 parity는 640창 전체에서 비교했고, 세부 클래스 정답은 581/640(90.78%)이다. 파일마다 첫 4창씩 40창이 투표 초기 이력으로 쓰여 4/5 이진 판정 분모는 600창이며, 고장 486/486창 탐지와 정상 0/114창 확정 오경보를 기록했다.

근거 코드는 현재 위치 `firmware_cwru_integration/src/em_cwru.c`의 `em_cwru_features()`, `em_cwru_predict()`, `em_cwru_vote_update()`와 `include/em_cwru.h`다. CWRU 실행 결과는 이 경로의 기록으로 정리된다.

## 코드·수식 근거

- 정확한 feature 이름·배열 순서와 fold provenance: 현재 위치 `fault_type_90_mechanical/a_features.py`, `fault_type_90_mechanical/a_group_develop.py`, `output/fault_type_90_mechanical_group_20261002/candidates/limited400__single_ch2__extended__linear_c1/feature_contract.json`, `fit_provenance.json`, `firmware_ml_evidence_live/verification/model_provenance.json`.
- 정의·순서·모델 마진: 로컬 `firmware_ml_evidence_live/src/ml_reference.c`의 `psd()`(47~53행), `ml_reference_extract()`(55~92행), `ml_reference_predict()`(94~102행); `tools/export_model.py`(18~24행); `src/ml_live.c`(6~35행).
- 구현과 개발 결과 요약: 로컬 `firmware_ml_evidence_live/README_KO.md`, `verification/ml_parity.json`, `verification/live_parity.json`, `output/fault_type_90_mechanical_group_20261002/SUMMARY_KO.md`.
- 현재 위치: `firmware_ml_evidence_live/src/ml_reference.c`, `firmware_ml_evidence_live/src/ml_live.c`, `firmware_ml_evidence_live/tools/verify_ml.py`, `fault_type_90_mechanical/`, `output/fault_type_90_mechanical_group_20261002/`, `firmware_cwru_integration/`.
