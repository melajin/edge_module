#ifndef EM_V3_H
#define EM_V3_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define EM_V3_WINDOW 5
#define EM_V3_REQUIRED 4

typedef enum {
    EM_V3_UNAVAILABLE = 0, EM_V3_NORMAL, EM_V3_CONFIRMED,
    EM_V3_SUSPECTED, EM_V3_INSPECTION_REQUIRED
} em_v3_status_t;

typedef struct { bool valid; bool flag; } em_v3_instant_t;
typedef struct {
    bool rpm_available, baseline_available;
    bool amplitude_present, phase_present;
    float amplitude_ratio_1x, phase_concentration;
} em_v3_imbalance_t;
typedef struct {
    em_v3_instant_t bearing, misalignment, belt;
    em_v3_imbalance_t imbalance;
} em_v3_input_t;
typedef struct {
    em_v3_status_t status;
    uint8_t votes_positive, votes_valid;
    bool instant_valid, instant_flag;
} em_v3_fault_result_t;
typedef struct {
    em_v3_fault_result_t bearing, misalignment, belt, imbalance;
    bool imbalance_auto_confirmation_allowed;
} em_v3_result_t;
typedef struct { uint8_t bits[EM_V3_WINDOW], count, next, positive; } em_v3_history_t;
typedef struct {
    em_v3_history_t bearing, misalignment, belt, imbalance;
    float imbalance_amplitude_ratio, imbalance_phase_concentration;
} em_v3_t;

/* Each instance belongs to exactly one equipment and one uninterrupted run.
 * Caller resets on new equipment/session, stop, input gap or baseline change. */
bool em_v3_init(em_v3_t *v, float amplitude_ratio, float phase_concentration);
void em_v3_reset(em_v3_t *v);
/* Returns false for malformed numeric input and leaves history untouched. */
bool em_v3_update(em_v3_t *v, const em_v3_input_t *in, em_v3_result_t *out);
void em_v3_unavailable(em_v3_result_t *out);
const char *em_v3_status_name(em_v3_status_t status);

#ifdef __cplusplus
}
#endif
#endif
