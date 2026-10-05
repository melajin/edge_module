#ifndef ML_LIVE_H
#define ML_LIVE_H
#include "v3_signal.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
 int label; double scores[4]; unsigned order_mask; float fs_hz,rpm;
 const char *reason;
} ml_live_result_t;
/* Serialized fixed workspace; no heap allocations. Latest 1 sec on actual timestamps. */
void ml_live_run(const v3_sample_t *samples,unsigned count,int axis,
                 const v3_signal_result_t *signal,int acquisition_ok,ml_live_result_t *out);
#ifdef __cplusplus
}
#endif
#endif
