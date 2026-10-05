#include "fault_evidence.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

#define PI 3.14159265358979323846
static fec_workspace_t scratch;
static fec_learner_t learner;
static fec_baseline_t baseline;
static fec_learner_t group_learner;
static float axes[3][FEC_FFT_SIZE];
static unsigned assertions, failures, cases;
static const char *current_case;
static FILE *artifact;
static uint32_t rng;

#define CHECK(expr) do { ++assertions; if (!(expr)) { ++failures; \
 fprintf(stderr, "FAIL %s line %d: %s\n", current_case, __LINE__, #expr); } } while (0)

static void begin_case(const char *name) { current_case = name; ++cases; }
static void end_case(unsigned before) {
    fprintf(artifact, "%s{\"name\":\"%s\",\"passed\":%s}", cases > 1 ? ",\n    " : "",
            current_case, failures == before ? "true" : "false");
}
static float noise(void) {
    rng = rng * UINT32_C(1664525) + UINT32_C(1013904223);
    return ((float)(rng >> 8) / 16777216.0f - 0.5f) * 2;
}
static fec_window_t window(float fs, float fr, uint64_t start) {
    fec_window_t w;
    memset(&w, 0, sizeof(w));
    w.axis[0] = axes[0]; w.axis[1] = axes[1]; w.axis[2] = axes[2];
    w.fs_hz = fs; w.fr_hz = fr; w.start_ms = start;
    w.end_ms = start + (uint64_t)(1000.0 * FEC_FFT_SIZE / fs + 0.5);
    w.quality_ok = 1;
    w.external[FEC_IMBALANCE].strength = 1;
    w.external[FEC_IMBALANCE].sources = FEC_SOURCE_FG_PHASE;
    w.external[FEC_IMBALANCE].observations = 5;
    w.external[FEC_IMBALANCE].quality_ok = 1;
    return w;
}
static void tones(float fs, float fr, float half, float one, float onehalf,
                  float two, float three, float upper, int irregular) {
    unsigned i;
    for (i = 0; i < FEC_FFT_SIZE; ++i) {
        double t = (double)i / fs;
        double envelope = irregular ? 0.15 + 1.7 * i / (FEC_FFT_SIZE - 1) : 1;
        axes[0][i] = (float)(envelope * (half * sin(2 * PI * 0.5 * fr * t) +
            one * sin(2 * PI * fr * t) + onehalf * sin(2 * PI * 1.5 * fr * t) +
            two * sin(2 * PI * 2 * fr * t) + three * sin(2 * PI * 3 * fr * t) +
            upper * sin(2 * PI * 120 * t)));
        axes[1][i] = axes[2][i] = 0;
    }
}
static void normal(float fs, float fr, uint32_t seed) {
    unsigned i; rng = seed;
    tones(fs, fr, 0, 0.1f, 0, 0.005f, 0, 0, 0);
    for (i = 0; i < FEC_FFT_SIZE; ++i) {
        axes[0][i] += 0.015f * noise(); axes[1][i] = 0.01f * noise(); axes[2][i] = 0.008f * noise();
    }
}
static void train(fec_config_t *c, float fs, float fr) {
    unsigned k; fec_result_t r; fec_window_t w;
    fec_learner_reset(&learner);
    for (k = 0; k < FEC_BASELINE_WINDOWS; ++k) {
        normal(fs, fr, 700 + k); w = window(fs, fr, (uint64_t)k * 2000);
        CHECK(fec_baseline_add(c, &w, &learner, &scratch, &r) == FEC_OK);
    }
    CHECK(fec_baseline_finish(c, &learner, &baseline) == FEC_OK);
}
static void check_sum(const fec_result_t *r) {
    unsigned j, k, counted = 0; double sum = 0, energy = 0;
    for (j = 0; j < FEC_BAND_COUNT; ++j) {
        counted += r->band_bins[j];
        if (r->band_available[j]) { sum += r->share_percent[j]; energy += r->energy[j]; }
        else { CHECK(isnan(r->share_percent[j])); CHECK(isnan(r->energy[j])); }
    }
    CHECK(fabs(sum - 100) < 0.0001);
    CHECK(fabs(energy - r->trusted_energy) < 0.00001 * r->trusted_energy);
    CHECK(counted == (unsigned)floorf(r->trusted_max_hz / r->bin_hz));
    for (k = 1; k <= counted; ++k) CHECK(scratch.bin_owner[k] >= 0 && scratch.bin_owner[k] < FEC_BAND_COUNT);
}

int main(int argc, char **argv) {
    fec_config_t c; fec_window_t w; fec_result_t r; fec_temporal_t temporal;
    unsigned before, j; float normal_max = 0, pure1_share = 0, pure2_share = 0;
    float imbal_strength = 0, loose_strength = 0, outside_coverage = 0;
    const char *path = argc > 1 ? argv[1] : "host_test_results.json";
    artifact = fopen(path, "wb"); if (!artifact) return 2;
    fprintf(artifact, "{\n  \"scope\":\"host software tests and synthetic signals; no hardware measurements\",\n  \"tests\":[\n    ");
    fec_config_default(&c);

    before = failures; begin_case("pure_1x_hann_mean_square_and_axis_sum");
    tones(400, 25, 0, 1, 0, 0, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    pure1_share = r.share_percent[FEC_ONE]; CHECK(pure1_share > 99.99f);
    CHECK(fabsf(r.trusted_energy - 0.5f) < 0.00001f);
    for (j = 0; j < FEC_FFT_SIZE; ++j) axes[1][j] = axes[0][j] * 2;
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK);
    CHECK(fabsf(r.trusted_energy - 2.5f) < 0.0001f); end_case(before);

    before = failures; begin_case("pure_2x_does_not_become_1x");
    tones(400, 25, 0, 0, 0, 1, 0, 0, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    pure2_share = r.share_percent[FEC_TWO]; CHECK(pure2_share > 99.99f); CHECK(r.share_percent[FEC_ONE] < 0.001f); end_case(before);

    before = failures; begin_case("mixed_1x_2x_energy_ratio_and_disjoint_upper");
    tones(400, 25, 0, 1, 0, 2, 0, 1, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    CHECK(fabsf(r.share_percent[FEC_ONE] - 100.0f / 6) < 0.01f);
    CHECK(fabsf(r.share_percent[FEC_TWO] - 400.0f / 6) < 0.01f);
    CHECK(fabsf(r.share_percent[FEC_UPPER] - 100.0f / 6) < 0.01f); end_case(before);

    before = failures; begin_case("half_and_one_half_orders");
    tones(400, 25, 1, 0, 2, 0, 0, 0, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    CHECK(fabsf(r.share_percent[FEC_HALF] - 20) < 0.01f); CHECK(fabsf(r.share_percent[FEC_ONE_HALF] - 80) < 0.01f); end_case(before);

    before = failures; begin_case("65hz_rotation_3x_195hz_outside_160hz_limit_and_coverage");
    tones(400, 65, 0, 1, 0, 0, 5, 0, 0); w = window(400, 65, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    CHECK(!r.band_available[FEC_THREE]); CHECK(r.band_reason[FEC_THREE] == FEC_OUTSIDE_TRUSTED_BAND);
    outside_coverage = r.trusted_coverage_percent; CHECK(outside_coverage < 4 && outside_coverage > 3); end_case(before);

    before = failures; begin_case("entire_order_mask_must_fit_limit");
    tones(400, 51, 0, 1, 0, 0, 0, 0, 0); w = window(400, 51, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK);
    CHECK(3 * w.fr_hz < r.trusted_max_hz); CHECK(!r.band_available[FEC_THREE]); end_case(before);

    before = failures; begin_case("overlapping_low_speed_masks_are_unavailable");
    tones(400, 6, 0, 1, 0, 0, 0, 0, 0); w = window(400, 6, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK); check_sum(&r);
    CHECK(!r.band_available[FEC_ONE]); CHECK(r.band_reason[FEC_ONE] == FEC_OVERLAPPING_ORDERS); end_case(before);

    before = failures; begin_case("zero_nonfinite_and_bad_quality_rejected");
    tones(400, 25, 0, 0, 0, 0, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_ZERO_ENERGY);
    axes[0][12] = NAN; CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_NONFINITE);
    axes[0][12] = INFINITY; CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_NONFINITE);
    w.quality_ok = 0; CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_BAD_QUALITY);
    fec_learner_reset(&learner); CHECK(fec_baseline_add(&c, &w, &learner, &scratch, &r) == FEC_BAD_QUALITY); CHECK(learner.count == 0); end_case(before);

    before = failures; begin_case("explicit_336_window_baseline_and_robust_scale_floor");
    fec_learner_reset(&learner);
    for (j = 0; j < 335; ++j) {
        normal(400, 25, 100 + j); w = window(400, 25, (uint64_t)j * 2000);
        CHECK(fec_baseline_add(&c, &w, &learner, &scratch, &r) == FEC_OK);
    }
    CHECK(fec_baseline_finish(&c, &learner, &baseline) == FEC_BASELINE_NOT_READY);
    CHECK(fec_baseline_add(&c, &w, &learner, &scratch, &r) == FEC_OK);
    CHECK(fec_baseline_finish(&c, &learner, &baseline) == FEC_OK);
    CHECK(fec_baseline_validate(&c, &baseline) == FEC_OK);
    CHECK(fec_baseline_add(&c, &w, &learner, &scratch, &r) == FEC_BASELINE_COMPLETE);
    CHECK(learner.count == 336);
    for (j = 0; j < FEC_FEATURE_COUNT; ++j) CHECK(baseline.log_scale[j] >= c.log_mad_floor);
    end_case(before);

    before = failures; begin_case("normal_learn_eval_same_windows_and_unseen_noise_no_forced_fault");
    for (j = 0; j < 128; ++j) {
        normal(400, 25, j < 64 ? 100 + j : 10000 + j); w = window(400, 25, (uint64_t)j * 2000);
        CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
        normal_max = fmaxf(normal_max, r.abnormality_percent);
        CHECK(r.abnormality_percent < 20); CHECK(!r.relative_visible);
        CHECK(r.strength_percent[FEC_IMBALANCE] < 20);
    } end_case(before);

    before = failures; begin_case("default_missing_candidates_are_null_with_reasons");
    CHECK(r.candidate_available[FEC_IMBALANCE]); CHECK(r.candidate_available[FEC_LOOSENESS]);
    CHECK(!r.candidate_available[FEC_MISALIGNMENT]); CHECK(r.candidate_reason[FEC_MISALIGNMENT] == FEC_MISSING_COUPLING);
    CHECK(!r.candidate_available[FEC_BEARING]); CHECK(r.candidate_reason[FEC_BEARING] == FEC_MISSING_BEARING_METADATA);
    CHECK(!r.candidate_available[FEC_BELT]); CHECK(r.candidate_reason[FEC_BELT] == FEC_MISSING_BELT_METADATA);
    CHECK(isnan(r.strength_percent[FEC_BEARING])); CHECK(isnan(r.relative_percent[FEC_IMBALANCE])); end_case(before);

    before = failures; begin_case("strong_1x_requires_fg_phase_and_strength_is_not_relative_share");
    tones(400, 25, 0, 1, 0, 0, 0, 0, 0); w = window(400, 25, 0);
    w.external[FEC_IMBALANCE].strength = 0.8f;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    imbal_strength = r.strength_percent[FEC_IMBALANCE]; CHECK(imbal_strength > 70 && imbal_strength < 90);
    CHECK(!r.relative_visible); /* Single-window evidence never claims persistence. */
    fec_temporal_reset(&temporal);
    for (j = 0; j < 3; ++j) {
        w.start_ms = j * 1280; w.end_ms = w.start_ms + 1280;
        CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
        CHECK(r.relative_visible == (j == 2));
    }
    CHECK(r.relative_percent[FEC_IMBALANCE] > 99);
    w.external[FEC_IMBALANCE].sources = 0;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(!r.candidate_available[FEC_IMBALANCE]); CHECK(r.candidate_reason[FEC_IMBALANCE] == FEC_MISSING_FG_PHASE); end_case(before);

    before = failures; begin_case("harmonic_only_does_not_diagnose_looseness_or_misalignment");
    tones(400, 25, 0, 0.1f, 0, 1, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(r.abnormality_percent > 90); CHECK(r.strength_percent[FEC_LOOSENESS] < 1);
    CHECK(!r.candidate_available[FEC_MISALIGNMENT]); CHECK(!r.relative_visible); end_case(before);

    before = failures; begin_case("higher_half_orders_plus_irregularity_support_looseness");
    tones(400, 25, 0.7f, 0.1f, 0.5f, 1, 0.5f, 0, 1); w = window(400, 25, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    loose_strength = r.strength_percent[FEC_LOOSENESS]; CHECK(loose_strength > 50); CHECK(!r.relative_visible); end_case(before);

    before = failures; begin_case("weak_evidence_relative_share_hidden");
    tones(400, 25, 0, 0.128f, 0, 0.005f, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(r.abnormality_percent < 20); CHECK(!r.relative_visible);
    fec_temporal_reset(&temporal);
    for (j = 0; j < 3; ++j) {
        w.start_ms = j * 1280; w.end_ms = w.start_ms + 1280;
        CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    }
    CHECK(!r.relative_visible); end_case(before);

    before = failures; begin_case("strong_abnormality_but_low_candidate_mass_relative_hidden");
    tones(400, 25, 0, 1, 0, 0, 0, 0, 0); fec_temporal_reset(&temporal);
    for (j = 0; j < 3; ++j) {
        w = window(400, 25, j * 1280); w.external[FEC_IMBALANCE].strength = 0.05f;
        CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    }
    CHECK(r.abnormality_percent > 90); CHECK(r.strength_percent[FEC_IMBALANCE] < 20); CHECK(!r.relative_visible); end_case(before);

    before = failures; begin_case("baseline_fs_rpm_configuration_and_corruption_checks");
    normal(400, 25, 10); w = window(400, 28, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_RPM_UNSUPPORTED);
    CHECK(r.baseline_status == FEC_RPM_UNSUPPORTED); CHECK(isnan(r.strength_percent[FEC_IMBALANCE]));
    w = window(440, 25, 0); CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_FS_UNSUPPORTED);
    ++c.pwm_command; CHECK(fec_baseline_validate(&c, &baseline) == FEC_CONFIG_CHANGED); --c.pwm_command;
    c.upper_band_min_hz += 1; CHECK(fec_baseline_validate(&c, &baseline) == FEC_CONFIG_CHANGED); c.upper_band_min_hz -= 1;
    baseline.log_median[0] += 1; CHECK(fec_baseline_validate(&c, &baseline) == FEC_BASELINE_NOT_READY); baseline.log_median[0] -= 1;
    /* Exact restored copy avoids roundoff from intentionally corrupting a float. */
    CHECK(fec_baseline_finish(&c, &learner, &baseline) == FEC_OK); end_case(before);

    before = failures; begin_case("temporal_continuity_gap_boundary_quality_and_monotonic_reset");
    fec_temporal_reset(&temporal); tones(400, 25, 0, 1, 0, 0, 0, 0, 0);
    w = window(400, 25, 0); CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(r.temporal_reset && r.observation_count == 1);
    w = window(400, 25, 1280); CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(!r.temporal_reset && r.observation_count == 2);
    w = window(400, 25, 3060); CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(!r.temporal_reset && r.observation_count == 3); CHECK(r.relative_visible); /* exactly 500ms gap */
    w = window(400, 25, 4841); CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(r.temporal_reset && r.observation_count == 1); CHECK(!r.relative_visible); /* 501ms gap */
    w = window(400, 25, 100); CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(r.temporal_reset && r.observation_count == 1);
    w.quality_ok = 0; CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_BAD_QUALITY); CHECK(!temporal.initialized); end_case(before);

    before = failures; begin_case("ewma_updates_only_valid_supported_candidates");
    fec_temporal_reset(&temporal); tones(400, 25, 0, 1, 0, 0, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    imbal_strength = r.strength_percent[FEC_IMBALANCE];
    normal(400, 25, 100); w = window(400, 25, 1280);
    CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(fabsf(r.strength_percent[FEC_IMBALANCE] - 0.6f * imbal_strength) < 0.01f);
    w = window(400, 25, 2560); w.external[FEC_IMBALANCE].quality_ok = 0;
    CHECK(fec_evaluate(&c, &w, &baseline, &temporal, &scratch, &r) == FEC_OK);
    CHECK(!r.candidate_available[FEC_IMBALANCE]); CHECK(temporal.candidate_count[FEC_IMBALANCE] == 0); end_case(before);

    before = failures; begin_case("baseline_axis_composition_change_rejected");
    normal(400, 25, 90); w = window(400, 25, 0); w.axis[1] = NULL;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_AXIS_SUPPORT_CHANGED);
    fec_learner_reset(&group_learner); w = window(400, 25, 0);
    CHECK(fec_baseline_add(&c, &w, &group_learner, &scratch, &r) == FEC_OK);
    w.axis[2] = NULL;
    CHECK(fec_baseline_add(&c, &w, &group_learner, &scratch, &r) == FEC_AXIS_SUPPORT_CHANGED);
    CHECK(group_learner.count == 1); end_case(before);

    before = failures; begin_case("radial_shock_shape_and_cv_count_once_as_irregularity_group");
    {
        fec_baseline_t group_baseline; unsigned row;
        tones(400, 25, 0, 0, 0, 0, 0, 0, 0); axes[0][256] = 2; w = window(400, 25, 0);
        CHECK(fec_extract(&c, &w, &scratch, &r) == FEC_OK);
        /* Controlled reference: hold all spectral log features exactly equal to
         * current extraction; retain normal shape/CV learning. Isolates the group. */
        memcpy(&group_learner, &learner, sizeof(learner));
        for (row = 0; row < FEC_BASELINE_WINDOWS; ++row)
            for (j = 0; j <= FEC_FEATURE_TOTAL_ENERGY; ++j) group_learner.logs[row][j] = logf(r.feature[j]);
        CHECK(fec_baseline_finish(&c, &group_learner, &group_baseline) == FEC_OK);
        CHECK(fec_evaluate(&c, &w, &group_baseline, NULL, &scratch, &r) == FEC_OK);
        CHECK(r.deviation[FEC_FEATURE_CREST] > 0.99f); CHECK(r.deviation[FEC_FEATURE_RMS_VARIABILITY] > 0.99f);
        CHECK(fabsf(r.abnormality_percent - 50) < 0.001f);
    } end_case(before);

    before = failures; begin_case("trained_band_availability_cannot_change_inside_rpm_tolerance");
    train(&c, 400, 50); normal(400, 51, 90); w = window(400, 51, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_BAND_SUPPORT_CHANGED); end_case(before);

    before = failures; begin_case("external_metadata_without_observations_is_unavailable");
    c.coupling_present = c.bearing_geometry_present = c.belt_metadata_present = 1;
    train(&c, 400, 25); tones(400, 25, 0, 0.1f, 0, 1, 0, 0, 0); w = window(400, 25, 0);
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(!r.candidate_available[FEC_MISALIGNMENT]); CHECK(!r.candidate_available[FEC_BEARING]); CHECK(!r.candidate_available[FEC_BELT]); end_case(before);

    before = failures; begin_case("optional_external_sources_gated_and_nonexclusive");
    w.external[FEC_MISALIGNMENT].strength = 0.75f; w.external[FEC_MISALIGNMENT].sources = FEC_SOURCE_DIRECTION;
    w.external[FEC_MISALIGNMENT].observations = 1; w.external[FEC_MISALIGNMENT].quality_ok = 1;
    w.external[FEC_BEARING].strength = 0.6f;
    w.external[FEC_BEARING].sources = FEC_SOURCE_REAL_ENVELOPE | FEC_SOURCE_BEARING_GEOMETRY;
    w.external[FEC_BEARING].observations = 1; w.external[FEC_BEARING].quality_ok = 1;
    w.external[FEC_BELT].strength = 0.4f; w.external[FEC_BELT].sources = FEC_SOURCE_TENSION;
    w.external[FEC_BELT].observations = 1; w.external[FEC_BELT].quality_ok = 1;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(r.candidate_available[FEC_MISALIGNMENT]); CHECK(r.candidate_available[FEC_BEARING]); CHECK(r.candidate_available[FEC_BELT]);
    CHECK(r.strength_percent[FEC_MISALIGNMENT] > 80); CHECK(fabsf(r.strength_percent[FEC_BEARING] - 60) < 0.01f);
    CHECK(fabsf(r.strength_percent[FEC_BELT] - 40) < 0.01f); CHECK(!r.relative_visible);
    CHECK(r.strength_percent[FEC_MISALIGNMENT] + r.strength_percent[FEC_BEARING] + r.strength_percent[FEC_BELT] > 100);
    w.external[FEC_BEARING].sources = FEC_SOURCE_REAL_ENVELOPE;
    w.external[FEC_BELT].strength = NAN;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(!r.candidate_available[FEC_BEARING]); CHECK(!r.candidate_available[FEC_BELT]); end_case(before);

    before = failures; begin_case("one_x_with_independent_direction_supports_nonexclusive_misalignment");
    tones(400, 25, 0, 1, 0, 0, 0, 0, 0); w = window(400, 25, 0);
    w.external[FEC_MISALIGNMENT].strength = 0.75f; w.external[FEC_MISALIGNMENT].sources = FEC_SOURCE_SECOND_LOCATION;
    w.external[FEC_MISALIGNMENT].observations = 1; w.external[FEC_MISALIGNMENT].quality_ok = 1;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(r.candidate_available[FEC_MISALIGNMENT]); CHECK(r.strength_percent[FEC_MISALIGNMENT] > 80);
    CHECK(r.strength_percent[FEC_IMBALANCE] > 90);
    w.external[FEC_MISALIGNMENT].sources = 0;
    CHECK(fec_evaluate(&c, &w, &baseline, NULL, &scratch, &r) == FEC_OK);
    CHECK(!r.candidate_available[FEC_MISALIGNMENT]); end_case(before);

    fprintf(artifact, "\n  ],\n  \"case_count\":%u,\n  \"assertions\":%u,\n  \"failures\":%u,\n", cases, assertions, failures);
    fprintf(artifact, "  \"metrics\":{\"pure_1x_share_percent\":%.8g,\"pure_2x_share_percent\":%.8g,\"normal_max_abnormality_percent\":%.8g,\"trusted_coverage_for_1x_plus_strong_195hz_percent\":%.8g,\"looseness_combined_evidence_percent\":%.8g},\n", pure1_share, pure2_share, normal_max, outside_coverage, loose_strength);
    fprintf(artifact, "  \"memory_bytes\":{\"workspace\":%llu,\"learner\":%llu,\"baseline\":%llu,\"temporal\":%llu,\"result\":%llu}\n}\n",
        (unsigned long long)sizeof(scratch), (unsigned long long)sizeof(learner), (unsigned long long)sizeof(baseline), (unsigned long long)sizeof(temporal), (unsigned long long)sizeof(r));
    fclose(artifact);
    printf("%u cases, %u assertions, %u failures\n", cases, assertions, failures);
    return failures ? 1 : 0;
}
