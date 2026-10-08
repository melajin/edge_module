#include "acquisition_quality.h"
#include <math.h>

void acquisition_record(AcquisitionQuality &w, uint64_t t, const v3_sample_t &sample)
{
    if (w.count == 0) w.start_us = t;
    else if (t <= w.end_us || t - w.end_us < 1800u || t - w.end_us > 3200u)
        w.time_error = true;
    w.end_us = t;
    ++w.count;
    for (unsigned axis = 0; axis < 3; ++axis) {
        if (!isfinite(sample.g[axis])) w.numeric_error = true;
        else if (fabsf(sample.g[axis]) >= 3.9f) w.range_error = true;
    }
}

void acquisition_finish(AcquisitionHistory &history, AcquisitionQuality &w)
{
    w.seq = history.next_seq++;
    if (w.count) {
        if (history.have_end && w.start_us >= history.last_end_us) {
            w.have_gap = true;
            w.gap_us = w.start_us - history.last_end_us;
        }
        history.last_end_us = w.end_us;
        history.have_end = true;
    }
    if (!w.count) w.reason = "no_samples";
    else if (w.count != V3_SAMPLE_COUNT) w.reason = "partial_window";
    else if (w.time_error || w.end_us <= w.start_us) w.reason = "sample_time";
    else if (w.numeric_error) w.reason = "numeric_input";
    else if (w.range_error) w.reason = "sensor_range";
    else {
        /* Same window rate/range limits as v3_signal, independent of FG/PPR,
         * baseline availability and phase-history warmup. */
        const float rate = (float)((w.count - 1u) * 1000000.0 /
                                   (w.end_us - w.start_us));
        w.reason = rate < V3_SAMPLE_RATE_MIN_HZ || rate > 420.0f
            ? "sample_rate" : "ok";
        w.valid = rate >= V3_SAMPLE_RATE_MIN_HZ && rate <= 420.0f;
    }
}
