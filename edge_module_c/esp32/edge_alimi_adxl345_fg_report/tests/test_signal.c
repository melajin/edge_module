#include "v3_signal.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define PI 3.14159265358979323846

static v3_sample_t samples[V3_SAMPLE_COUNT];
static v3_fg_edge_t edges[V3_FG_RING_SIZE];

static unsigned synthetic(uint32_t origin)
{
    const double pulse_us = 7000.0;
    unsigned count = 0;
    for (int k = -2; k < 210; ++k) {
        edges[count].time_us = origin + (uint32_t)(k * 7000);
        edges[count].ordinal = (uint32_t)(1000 + k);
        ++count;
    }
    for (unsigned i = 0; i < V3_SAMPLE_COUNT; ++i) {
        const double t = i * 2500.0;
        const double theta = PI * (1000.0 + t / pulse_us);
        samples[i].time_us = origin + (uint32_t)t;
        samples[i].g[0] = (float)(0.30 + 0.12 * cos(theta + 0.4));
        samples[i].g[1] = (float)(-0.2 + 0.07 * cos(theta - 0.7));
        samples[i].g[2] = 0.03f;
    }
    return count;
}

static void fast_fg_eight_ppr(uint32_t origin, unsigned *edge_count)
{
    /* 8 PPR at about 69.4 rps: ~711 FG edges in one 512-sample window. */
    unsigned count = 0;
    for (int k = -2; k < 800; ++k) {
        edges[count].time_us = origin + (uint32_t)(k * 1800);
        edges[count].ordinal = (uint32_t)(1000 + k);
        ++count;
    }
    for (unsigned i = 0; i < V3_SAMPLE_COUNT; ++i) {
        const double t = i * 2500.0;
        const double theta = (2.0 * PI / 8.0) * (1000.0 + t / 1800.0);
        samples[i].time_us = origin + (uint32_t)t;
        samples[i].g[0] = (float)(0.3 + 0.12 * cos(theta + 0.4));
        samples[i].g[1] = 0.0f;
        samples[i].g[2] = 0.0f;
    }
    *edge_count = count;
}

static void ordinal_32bit_boundary(uint32_t origin, unsigned *edge_count)
{
    const uint64_t base = (uint64_t)UINT32_MAX - 100u;
    unsigned count = 0;
    for (int k = -2; k < 300; ++k) {
        edges[count].time_us = origin + (uint32_t)(k * 4800);
        edges[count].ordinal = base + k;
        ++count;
    }
    for (unsigned i = 0; i < V3_SAMPLE_COUNT; ++i) {
        const double t = i * 2500.0;
        const double theta = (2.0 * PI / 3.0) *
                             ((base % 3u) + t / 4800.0);
        samples[i].time_us = origin + (uint32_t)t;
        samples[i].g[0] = (float)(0.2 + 0.12 * cos(theta + 0.4));
        samples[i].g[1] = samples[i].g[2] = 0.0f;
    }
    *edge_count = count;
}

static void expect_close(float got, float expected, float tolerance)
{
    if (fabsf(got - expected) > tolerance) {
        fprintf(stderr, "got %.6f expected %.6f (tol %.6f)\n",
                got, expected, tolerance);
        assert(0);
    }
}

int main(void)
{
    /* The ring must preserve the prior-window FG edge through the longest
     * accepted 100 ms interwindow gap plus a full 380 Hz sample window,
     * even at the accepted 1 ms FG interval. Match MAX_INTERWINDOW_GAP_US
     * in edge_alimi_adxl345_v3.ino when changing this contract. */
    assert(V3_FG_RING_SIZE >
           2u + ((V3_SAMPLE_COUNT - 1u) * 1000000u /
                 V3_SAMPLE_RATE_MIN_HZ + 100000u) /
                    V3_FG_MIN_INTERVAL_US);
    v3_signal_result_t result;
    unsigned n = synthetic(100000u);
    assert(v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2, 0, n, &result));
    assert(result.reason == V3_SIGNAL_OK);
    expect_close(result.sample_rate_hz, 400.0f, 0.01f);
    expect_close(result.fg_hz, 1000000.0f / 7000.0f, 0.01f);
    expect_close(result.amplitude_1x_g[0], 0.12f, 0.002f);
    expect_close(result.amplitude_1x_g[1], 0.07f, 0.002f);
    expect_close(result.phase_1x_rad[0], 0.4f, 0.03f);
    expect_close(result.phase_1x_rad[1], -0.7f, 0.03f);

    const uint32_t next_start = samples[V3_SAMPLE_COUNT - 1].time_us + 30000u;
    assert(v3_fg_gap_contiguous(edges, n, result.last_fg_edge,
                                result.fg_period_us, next_start));
    unsigned after_previous = 0;
    while (after_previous < n &&
           edges[after_previous].ordinal <= result.last_fg_edge.ordinal)
        ++after_previous;
    assert(after_previous < n);
    edges[after_previous].time_us += 7000u; /* Missed pulse in the gap. */
    assert(!v3_fg_gap_contiguous(edges, n, result.last_fg_edge,
                                 result.fg_period_us, next_start));
    n = synthetic(100000u);
    assert(!v3_fg_gap_contiguous(edges + n - 2, 2, result.last_fg_edge,
                                 result.fg_period_us, next_start));

    fast_fg_eight_ppr(100000u, &n);
    assert(n > 512 && n < V3_FG_RING_SIZE);
    assert(v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 8, 0, n, &result));
    expect_close(result.amplitude_1x_g[0], 0.12f, 0.002f);
    expect_close(result.phase_1x_rad[0], 0.4f, 0.03f);

    ordinal_32bit_boundary(100000u, &n);
    assert(v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 3, 0, n, &result));
    assert(result.last_fg_edge.ordinal > UINT32_MAX);
    expect_close(result.amplitude_1x_g[0], 0.12f, 0.002f);
    expect_close(result.phase_1x_rad[0], 0.4f, 0.03f);

    n = synthetic(UINT32_MAX - 100000u);
    assert(v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2, 0, n, &result));
    expect_close(result.amplitude_1x_g[0], 0.12f, 0.002f);

    n = synthetic(100000u);
    samples[20].time_us += 4000u;
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2, 0, n, &result));
    assert(result.reason == V3_SIGNAL_TIME_AXIS);

    n = synthetic(100000u);
    samples[20].g[0] = 4.0f;
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2, 0, n, &result));
    assert(result.reason == V3_SIGNAL_SENSOR_RANGE);

    n = synthetic(100000u);
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 0, 0, n, &result));
    assert(result.reason == V3_SIGNAL_BAD_ARGUMENT);

    edges[20].time_us += 3500u;
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2, 0, n, &result));
    assert(result.reason == V3_SIGNAL_FG_UNSTABLE);

    n = synthetic(100000u);
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges + 20, n - 20, 2,
                          0, n, &result));
    assert(result.reason == V3_SIGNAL_FG_MISSING);

    /* A previous ring can have the same 32-bit micros() values one full
     * 2^32-us cycle later. With no new ISR edges, it must stay unavailable. */
    n = synthetic(100000u);
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2,
                          4200u, 4200u, &result));
    assert(result.reason == V3_SIGNAL_FG_MISSING);
    assert(!v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2,
                          4200u, 4202u, &result));
    assert(result.reason == V3_SIGNAL_FG_MISSING);
    assert(v3_analyze_1x(samples, V3_SAMPLE_COUNT, edges, n, 2,
                         4200u, 4203u, &result));
    assert(result.reason == V3_SIGNAL_OK);

    puts("v3_signal synthetic tests passed");
    return 0;
}
