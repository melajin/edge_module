#ifndef ML_REFERENCE_H
#define ML_REFERENCE_H
#ifdef __cplusplus
extern "C" {
#endif
/* Offline public-channel2 contract only: 400 resample_poly samples, 1 second,
 * publisher numeric units, nominal 1238 RPM. No ADXL345 equivalence established. */
int ml_reference_init(void);
int ml_reference_extract(const double samples[400], double rpm, double features[101]);
int ml_reference_predict(const double features[101], double scores[4]);
extern const char *const ml_reference_classes[4];
#ifdef __cplusplus
}
#endif
#endif
