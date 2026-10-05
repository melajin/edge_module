#ifndef BEARING_ADAPTER_H
#define BEARING_ADAPTER_H
#include "em_cwru.h"
#include "em_v3.h"
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define BEARING_STRIDE 2048u
#define BEARING_FRESHNESS_MS 2000u
/* Profile: one acceleration channel in the frozen model's g convention,
 * 12000 Hz, N=4096, stride=2048. HF numerator 10X..4800 Hz; total
 * denominator includes bins >1 Hz up to just below 6000 Hz. Sensor
 * response, anti-aliasing, scaling and installation need revalidation. */
typedef struct {
    uint64_t session, sequence, sample_start;
    double sample_rate_hz, rated_rpm;
    uint32_t dropped_samples;
} bearing_input;
typedef struct {
    double features[6], scores[4];
    int class_index;
    em_v3_fault_result_t bearing;
} bearing_output;
typedef struct {
    em_v3_t policy; /* independent cadence: exactly one update per raw window */
    bearing_output output;
    uint64_t session, sequence, sample_start;
    double rated_rpm;
    uint32_t last_update_ms;
    bool have_window;
} bearing_adapter;
/* 0 accepted, 1 accepted after continuity reset; negative = rejected and
 * history invalidated. All functions run in one serialized caller task. */
void bearing_init(bearing_adapter *state);
void bearing_reset(bearing_adapter *state);
int bearing_submit(bearing_adapter *state, const bearing_input *input,
                   const double *samples, size_t count,
                   em_cwru_scratch *scratch, uint32_t now_ms);
void bearing_expire(bearing_adapter *state, uint32_t now_ms);
void bearing_merge(const bearing_adapter *state, em_v3_result_t *low_result);
#ifdef __cplusplus
}
#endif
#endif
