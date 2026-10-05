#ifndef BEARING_FIRMWARE_H
#define BEARING_FIRMWARE_H
#include "bearing_adapter.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Future sensor/DMA caller submits calibrated windows from the Arduino task.
 * Not ISR-safe/thread-safe. No allocation or sensor driver is provided. */
int bearing_firmware_submit(const bearing_input *input, const double *samples, size_t count);
#ifdef __cplusplus
}
#endif
#endif
