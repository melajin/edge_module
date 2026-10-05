#ifndef FAULT_EVIDENCE_H
#define FAULT_EVIDENCE_H

#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define FEC_FFT_SIZE 512
#define FEC_FFT_BINS 257
#define FEC_BASELINE_WINDOWS 336
#define FEC_FEATURE_COUNT 11
#define FEC_BAND_COUNT 7
#define FEC_CANDIDATE_COUNT 5
#define FEC_BASELINE_MAGIC UINT32_C(0x46454331)
#define FEC_ABI_VERSION 1

typedef enum { FEC_HALF, FEC_ONE, FEC_ONE_HALF, FEC_TWO, FEC_THREE,
               FEC_UPPER, FEC_OTHER } fec_band_t;
typedef enum { FEC_IMBALANCE, FEC_LOOSENESS, FEC_MISALIGNMENT,
               FEC_BEARING, FEC_BELT } fec_candidate_t;
typedef enum { FEC_FEATURE_HALF_ENERGY, FEC_FEATURE_ONE_ENERGY,
               FEC_FEATURE_ONE_HALF_ENERGY, FEC_FEATURE_TWO_ENERGY,
               FEC_FEATURE_THREE_ENERGY, FEC_FEATURE_UPPER_ENERGY,
               FEC_FEATURE_OTHER_ENERGY, FEC_FEATURE_TOTAL_ENERGY,
               FEC_FEATURE_RADIAL_KURTOSIS, FEC_FEATURE_CREST,
               FEC_FEATURE_RMS_VARIABILITY } fec_feature_t;
typedef enum { FEC_OK, FEC_INVALID_ARGUMENT, FEC_BAD_QUALITY, FEC_NONFINITE,
               FEC_ZERO_ENERGY, FEC_OUTSIDE_TRUSTED_BAND, FEC_OVERLAPPING_ORDERS,
               FEC_EMPTY_BAND, FEC_BASELINE_NOT_READY, FEC_CONFIG_CHANGED,
               FEC_FS_UNSUPPORTED, FEC_RPM_UNSUPPORTED, FEC_BAND_SUPPORT_CHANGED,
               FEC_MISSING_FG_PHASE, FEC_MISSING_COUPLING,
               FEC_MISSING_DIRECTION_OR_LOCATION, FEC_MISSING_BEARING_METADATA,
               FEC_MISSING_ENVELOPE_OR_GEOMETRY, FEC_MISSING_BELT_METADATA,
               FEC_MISSING_TRANSMISSION_EVIDENCE, FEC_EXTERNAL_BAD_QUALITY,
               FEC_BASELINE_COMPLETE, FEC_AXIS_SUPPORT_CHANGED } fec_reason_t;

enum {
    FEC_SOURCE_FG_PHASE = 1u << 0,
    FEC_SOURCE_DIRECTION = 1u << 1,
    FEC_SOURCE_SECOND_LOCATION = 1u << 2,
    FEC_SOURCE_REAL_ENVELOPE = 1u << 3,
    FEC_SOURCE_BEARING_GEOMETRY = 1u << 4,
    FEC_SOURCE_TRANSMISSION = 1u << 5,
    FEC_SOURCE_TENSION = 1u << 6,
    FEC_SOURCE_BELT_CYCLE = 1u << 7
};
/* DIRECTION/SECOND_LOCATION mean measured directional/spatial patterns, not
 * merely named axes. TRANSMISSION means an independent shaft speed ratio/slip
 * measurement, not the presence of a belt. REAL_ENVELOPE|BEARING_GEOMETRY means
 * actual envelope periodicity compared with geometry-derived frequencies. */

/* Design constants are initialized by fec_config_default(). Any change requires
 * a fresh baseline; config_id/PWM/PPR are also part of the configuration hash. */
typedef struct {
    float sensor_max_hz, upper_band_min_hz;
    float energy_floor, log_mad_floor;
    float z_start, z_full, support_tolerance;
    float relative_abnormality_min, relative_strength_sum_min, ewma_alpha;
    uint32_t max_gap_ms, relative_min_windows, config_id, pwm_command, fg_pulses_per_rev;
    uint8_t coupling_present, bearing_geometry_present, belt_metadata_present;
} fec_config_t;

/* Physical strength is [0,1] evidence calibrated against that channel's own
 * normal reference, NOT a fault probability. The FG_PHASE exception is direct
 * circular phase coherence
 * over >=5 FG-synchronous windows. Metadata flags do not create observations. */
typedef struct {
    float strength;
    uint32_t sources;
    uint32_t observations;
    uint8_t quality_ok;
} fec_external_evidence_t;

typedef struct {
    const float *axis[3]; /* At least one axis; each non-null pointer has 512 samples. */
    float fs_hz, fr_hz;  /* Actual sampling rate and FG rotation rate. */
    uint64_t start_ms, end_ms; /* Monotonic window boundaries, including gaps. */
    uint8_t quality_ok;  /* Caller verifies uniform/resampled timing, missing
                         * samples, clipping and FG; average fs alone is insufficient. */
    fec_external_evidence_t external[FEC_CANDIDATE_COUNT];
} fec_window_t;

typedef struct {
    uint32_t magic, abi_version, config_hash, generation, count, feature_mask, axis_mask;
    float fs_min_hz, fs_max_hz, fr_min_hz, fr_max_hz;
    float log_median[FEC_FEATURE_COUNT], log_scale[FEC_FEATURE_COUNT];
} fec_baseline_t;

/* Bounded-memory explicit learner; never learn evaluation windows implicitly. */
typedef struct {
    float logs[FEC_BASELINE_WINDOWS][FEC_FEATURE_COUNT];
    float scratch[FEC_BASELINE_WINDOWS];
    uint32_t count, config_hash, feature_mask, axis_mask;
    float fs_min_hz, fs_max_hz, fr_min_hz, fr_max_hz;
    float reference_fs_hz, reference_fr_hz;
} fec_learner_t;

/* Scratch owned by caller; no allocation, hidden globals, or large stack arrays. */
typedef struct {
    float re[FEC_FFT_SIZE], im[FEC_FFT_SIZE];
    float power[FEC_FFT_BINS], radial2[FEC_FFT_SIZE];
    int8_t bin_owner[FEC_FFT_BINS];
} fec_workspace_t;

typedef struct {
    uint8_t initialized;
    uint32_t config_hash, baseline_generation, observation_count;
    uint32_t candidate_count[FEC_CANDIDATE_COUNT];
    uint64_t first_start_ms, last_end_ms;
    float abnormality, strength[FEC_CANDIDATE_COUNT];
} fec_temporal_t;

typedef struct {
    fec_reason_t status, baseline_status;
    uint32_t feature_mask, axis_mask;
    uint8_t band_available[FEC_BAND_COUNT];
    fec_reason_t band_reason[FEC_BAND_COUNT];
    uint16_t band_bins[FEC_BAND_COUNT];
    float energy[FEC_BAND_COUNT], share_percent[FEC_BAND_COUNT];
    float trusted_min_hz, trusted_max_hz, bin_hz, full_nyquist_energy;
    float trusted_energy, trusted_coverage_percent;
    float feature[FEC_FEATURE_COUNT];
    float deviation[FEC_FEATURE_COUNT]; /* Positive z ramp in [0,1], not raw z. */
    float abnormality_percent;
    uint8_t candidate_available[FEC_CANDIDATE_COUNT];
    fec_reason_t candidate_reason[FEC_CANDIDATE_COUNT];
    float strength_percent[FEC_CANDIDATE_COUNT];
    uint8_t relative_visible;
    float relative_percent[FEC_CANDIDATE_COUNT];
    uint32_t observation_count, candidate_observation_count[FEC_CANDIDATE_COUNT];
    uint64_t observation_start_ms, observation_end_ms;
    uint8_t temporal_reset;
} fec_result_t;

/* Unavailable numeric outputs use NAN AND availability/reason flags. JSON
 * serializers must emit null for unavailable fields (JSON does not allow NAN). */
void fec_config_default(fec_config_t *config);
void fec_learner_reset(fec_learner_t *learner);
void fec_temporal_reset(fec_temporal_t *temporal);
uint32_t fec_config_hash(const fec_config_t *config);
fec_reason_t fec_baseline_validate(const fec_config_t *, const fec_baseline_t *);
fec_reason_t fec_extract(const fec_config_t *, const fec_window_t *,
                         fec_workspace_t *, fec_result_t *);
fec_reason_t fec_baseline_add(const fec_config_t *, const fec_window_t *,
                              fec_learner_t *, fec_workspace_t *, fec_result_t *);
fec_reason_t fec_baseline_finish(const fec_config_t *, fec_learner_t *,
                                 fec_baseline_t *);
fec_reason_t fec_evaluate(const fec_config_t *, const fec_window_t *,
                          const fec_baseline_t *, fec_temporal_t *,
                          fec_workspace_t *, fec_result_t *);
const char *fec_reason_name(fec_reason_t);
const char *fec_band_name(fec_band_t);
const char *fec_candidate_name(fec_candidate_t);
const char *fec_feature_name(fec_feature_t);

#ifdef __cplusplus
}
#endif
#endif
