#ifndef V3_SIGNAL_H
#define V3_SIGNAL_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define V3_SAMPLE_COUNT 512u
#define V3_FG_RING_SIZE 2048u
#define V3_FG_MIN_INTERVAL_US 1000u
#define V3_SAMPLE_RATE_MIN_HZ 380u

typedef struct {
    uint32_t time_us;
    float g[3];
} v3_sample_t;

/* ordinal is a continuous, zero-based index of accepted FG edges. */
typedef struct {
    uint32_t time_us;
    uint64_t ordinal;
} v3_fg_edge_t;

typedef enum {
    V3_SIGNAL_OK = 0,
    V3_SIGNAL_BAD_ARGUMENT,
    V3_SIGNAL_TIME_AXIS,
    V3_SIGNAL_SENSOR_RANGE,
    V3_SIGNAL_FG_MISSING,
    V3_SIGNAL_FG_UNSTABLE
} v3_signal_reason_t;

typedef struct {
    v3_signal_reason_t reason;
    float sample_rate_hz;
    float fg_hz;
    float rpm;
    float amplitude_1x_g[3];
    float phase_1x_rad[3];
    v3_fg_edge_t last_fg_edge;
    float fg_period_us;
} v3_signal_result_t;

/* All samples must represent fresh ADXL345 conversions. The caller checks
 * DATA_READY and transport errors; this function checks their timestamps and
 * FG edge continuity. ppr=0 leaves 1x unavailable. */
bool v3_analyze_1x(const v3_sample_t *samples, unsigned sample_count,
                   const v3_fg_edge_t *edges, unsigned edge_count,
                   unsigned ppr, uint64_t fg_count_at_window_start,
                   uint64_t fg_count_at_window_end,
                   v3_signal_result_t *out);
/* Reject FG gaps and missing pulses between two otherwise valid windows. */
bool v3_fg_gap_contiguous(const v3_fg_edge_t *edges, unsigned edge_count,
                          v3_fg_edge_t previous_edge,
                          float previous_period_us,
                          uint32_t next_window_start_us);
const char *v3_signal_reason_name(v3_signal_reason_t reason);

#ifdef __cplusplus
}
#endif
#endif
