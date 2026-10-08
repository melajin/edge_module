#ifndef ML_REFERENCE_H
#define ML_REFERENCE_H
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Offline public-channel2 contract only: 400 resample_poly samples, 1 second,
 * publisher numeric units, nominal 1238 RPM. No ADXL345 equivalence established. */
typedef enum {
 ML_REFERENCE_UNINITIALIZED = 0,
 ML_REFERENCE_ALLOCATION_FAILED,
 ML_REFERENCE_READY
} ml_reference_resource_state_t;
/* Caller serializes init/extract and live inference: the shared workspace is
 * non-reentrant. Init allocates workspace_bytes() on the heap once, retains it
 * for process lifetime, and retries after failure; ready calls do not allocate. */
int ml_reference_init(void);
/* Read-only state/sizeof-based heap requirement; these calls do not allocate.
 * Serialize state reads with init (no synchronization is provided). */
ml_reference_resource_state_t ml_reference_resource_state(void);
size_t ml_reference_workspace_bytes(void);
int ml_reference_extract(const double samples[400], double rpm, double features[101]);
int ml_reference_predict(const double features[101], double scores[4]);
extern const char *const ml_reference_classes[4];
#ifdef __cplusplus
}
#endif
#endif
