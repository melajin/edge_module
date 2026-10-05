#ifndef EM_CWRU_H
#define EM_CWRU_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
#define EM_CWRU_N 4096
#define EM_CWRU_FEATURES 6
#define EM_CWRU_CLASSES 4
/* Caller-owned, static/heap recommended: 65536 bytes. No dynamic allocation. */
typedef struct { double real[EM_CWRU_N], imag[EM_CWRU_N]; } em_cwru_scratch;
/* Fixed 12 kHz profile. Returns 0 success, -1 invalid input/numerics.
 * Output is unchanged on failure; scratch may change. Flat signals are invalid
 * (Python's scipy kurtosis is NaN). No Hann window is applied. */
int em_cwru_features(const double *samples, size_t count, double sample_rate_hz,
                     double rated_rpm, em_cwru_scratch *scratch, double features[6]);
/* Frozen StandardScaler + logistic argmax; optional decision[4].
 * Classes 0=ball, 1=inner_race, 2=normal, 3=outer_race. */
int em_cwru_predict(const double features[6], int *class_index, double decision[4]);
typedef struct { unsigned char history[5], count, next; } em_cwru_vote;
void em_cwru_vote_reset(em_cwru_vote *state);
/* Reset per file; evaluable=false for first four windows. */
int em_cwru_vote_update(em_cwru_vote *state, int class_index, int *evaluable, int *confirmed);
#ifdef __cplusplus
}
#endif
#endif
