#ifndef ACQUISITION_QUALITY_H
#define ACQUISITION_QUALITY_H

#include "v3_signal.h"

/* Read-start clock, not the sensor conversion clock. Keep this history outside
 * diagnostic/FG resets: a stop or failed attempt does not erase observation. */
struct AcquisitionHistory {
    uint64_t next_seq = 0, last_end_us = 0;
    bool have_end = false;
};

struct AcquisitionQuality {
    uint64_t seq = 0, start_us = 0, end_us = 0, gap_us = 0;
    unsigned count = 0;
    bool have_gap = false, valid = false;
    const char *reason = "no_samples";
    bool time_error = false, numeric_error = false, range_error = false;
};

void acquisition_record(AcquisitionQuality &window, uint64_t read_start_us,
                        const v3_sample_t &sample);
void acquisition_finish(AcquisitionHistory &history, AcquisitionQuality &window);

#endif
