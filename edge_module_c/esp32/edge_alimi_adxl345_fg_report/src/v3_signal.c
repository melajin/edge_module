#include "v3_signal.h"

#include <math.h>
#include <string.h>

#define V3_PI 3.14159265358979323846f

static int32_t relative_us(uint32_t time_us, uint32_t origin_us)
{
    return (int32_t)(time_us - origin_us);
}

bool v3_analyze_1x(const v3_sample_t *samples, unsigned sample_count,
                   const v3_fg_edge_t *edges, unsigned edge_count,
                   unsigned ppr, uint64_t fg_count_at_window_start,
                   uint64_t fg_count_at_window_end,
                   v3_signal_result_t *out)
{
    if (!out) return false;
    memset(out, 0, sizeof(*out));
    out->reason = V3_SIGNAL_BAD_ARGUMENT;
    if (!samples || sample_count != V3_SAMPLE_COUNT || !edges ||
        edge_count < 4 || ppr == 0 || ppr > 16) return false;
    /* 32-bit micros() timestamps alias every 2^32 us. Require the independent
     * 64-bit ISR counter to prove FG edges arrived during this very window. */
    if (fg_count_at_window_end < fg_count_at_window_start ||
        fg_count_at_window_end - fg_count_at_window_start < 3u) {
        out->reason = V3_SIGNAL_FG_MISSING;
        return false;
    }

    const uint32_t origin = samples[0].time_us;
    int32_t last_sample_t = 0;
    double means[3] = {0, 0, 0};
    for (unsigned i = 0; i < sample_count; ++i) {
        const int32_t t = relative_us(samples[i].time_us, origin);
        if (i > 0 && (t <= last_sample_t || t - last_sample_t < 1800 ||
                      t - last_sample_t > 3200)) {
            out->reason = V3_SIGNAL_TIME_AXIS;
            return false;
        }
        last_sample_t = t;
        for (unsigned axis = 0; axis < 3; ++axis) {
            const float value = samples[i].g[axis];
            if (!isfinite(value) || fabsf(value) >= 3.9f) {
                out->reason = V3_SIGNAL_SENSOR_RANGE;
                return false;
            }
            means[axis] += value;
        }
    }
    out->sample_rate_hz = (float)((sample_count - 1u) * 1000000.0 / last_sample_t);
    if (out->sample_rate_hz < V3_SAMPLE_RATE_MIN_HZ ||
        out->sample_rate_hz > 420.0f) {
        out->reason = V3_SIGNAL_TIME_AXIS;
        return false;
    }
    for (unsigned axis = 0; axis < 3; ++axis) means[axis] /= sample_count;

    /* Select the last FG edge preceding the first sample, then all edges in
     * the window. The last partial pulse interval is extrapolated only for
     * less than one measured FG period. */
    int first = -1, last = -1;
    for (unsigned i = 0; i < edge_count; ++i) {
        const int32_t t = relative_us(edges[i].time_us, origin);
        if (t <= 0) first = (int)i;
        if (t <= last_sample_t) last = (int)i;
    }
    if (first < 0 || last - first < 3) {
        out->reason = V3_SIGNAL_FG_MISSING;
        return false;
    }
    double interval_sum = 0.0;
    for (int i = first + 1; i <= last; ++i) {
        const int32_t dt = relative_us(edges[i].time_us, edges[i - 1].time_us);
        if (edges[i].ordinal != edges[i - 1].ordinal + 1u ||
            dt < (int32_t)V3_FG_MIN_INTERVAL_US || dt > 100000) {
            out->reason = V3_SIGNAL_FG_UNSTABLE;
            return false;
        }
        interval_sum += dt;
    }
    const double mean_interval = interval_sum / (unsigned)(last - first);
    for (int i = first + 1; i <= last; ++i) {
        const double dt = relative_us(edges[i].time_us, edges[i - 1].time_us);
        if (fabs(dt - mean_interval) > 0.15 * mean_interval) {
            out->reason = V3_SIGNAL_FG_UNSTABLE;
            return false;
        }
    }
    if (last_sample_t - relative_us(edges[last].time_us, origin) >
        1.1 * mean_interval) {
        out->reason = V3_SIGNAL_FG_MISSING;
        return false;
    }
    out->fg_hz = (float)(1000000.0 / mean_interval);
    out->rpm = out->fg_hz * 60.0f / ppr;
    out->last_fg_edge = edges[last];
    out->fg_period_us = (float)mean_interval;

    double re[3] = {0, 0, 0}, im[3] = {0, 0, 0};
    int edge_index = first;
    for (unsigned i = 0; i < sample_count; ++i) {
        const int32_t sample_t = relative_us(samples[i].time_us, origin);
        while (edge_index < last &&
               relative_us(edges[edge_index + 1].time_us, origin) <= sample_t)
            ++edge_index;
        const int32_t edge_t = relative_us(edges[edge_index].time_us, origin);
        double interval = mean_interval;
        if (edge_index < last)
            interval = relative_us(edges[edge_index + 1].time_us,
                                   edges[edge_index].time_us);
        const double fraction = (sample_t - edge_t) / interval;
        if (fraction < 0.0 || fraction > 1.1) {
            out->reason = V3_SIGNAL_FG_MISSING;
            return false;
        }
        const double angle = (2.0 * V3_PI / ppr) *
                             ((edges[edge_index].ordinal % ppr) + fraction);
        const double c = cos(angle), s = sin(angle);
        for (unsigned axis = 0; axis < 3; ++axis) {
            const double centered = samples[i].g[axis] - means[axis];
            re[axis] += centered * c;
            im[axis] -= centered * s;
        }
    }
    for (unsigned axis = 0; axis < 3; ++axis) {
        out->amplitude_1x_g[axis] =
            (float)(2.0 * hypot(re[axis], im[axis]) / sample_count);
        out->phase_1x_rad[axis] = (float)atan2(im[axis], re[axis]);
    }
    out->reason = V3_SIGNAL_OK;
    return true;
}

bool v3_fg_gap_contiguous(const v3_fg_edge_t *edges, unsigned edge_count,
                          v3_fg_edge_t previous_edge,
                          float previous_period_us,
                          uint32_t next_window_start_us)
{
    if (!edges || !edge_count || !isfinite(previous_period_us) ||
        previous_period_us < V3_FG_MIN_INTERVAL_US ||
        previous_period_us > 100000.0f) return false;
    unsigned previous_index = edge_count;
    for (unsigned i = 0; i < edge_count; ++i) {
        if (edges[i].ordinal == previous_edge.ordinal &&
            edges[i].time_us == previous_edge.time_us) {
            previous_index = i;
            break;
        }
    }
    if (previous_index == edge_count) return false; /* Ring overrun. */
    double period = previous_period_us;
    unsigned last_index = previous_index;
    for (unsigned i = previous_index + 1; i < edge_count; ++i) {
        if (relative_us(edges[i].time_us, next_window_start_us) > 0) break;
        const int32_t dt = relative_us(edges[i].time_us,
                                       edges[last_index].time_us);
        if (edges[i].ordinal != edges[last_index].ordinal + 1u ||
            dt < (int32_t)V3_FG_MIN_INTERVAL_US || dt > 100000 ||
            fabs(dt - period) > 0.15 * period) return false;
        period = dt;
        last_index = i;
    }
    const int32_t tail_us =
        relative_us(next_window_start_us, edges[last_index].time_us);
    return tail_us >= 0 && tail_us <= 1.1 * period;
}

const char *v3_signal_reason_name(v3_signal_reason_t reason)
{
    switch (reason) {
    case V3_SIGNAL_OK: return "ok";
    case V3_SIGNAL_BAD_ARGUMENT: return "bad_argument_or_ppr";
    case V3_SIGNAL_TIME_AXIS: return "sample_time_axis";
    case V3_SIGNAL_SENSOR_RANGE: return "sensor_range";
    case V3_SIGNAL_FG_MISSING: return "fg_missing";
    case V3_SIGNAL_FG_UNSTABLE: return "fg_unstable";
    default: return "unknown";
    }
}
