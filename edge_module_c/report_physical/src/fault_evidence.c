#include "fault_evidence.h"

#include <float.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#define FEC_PI 3.14159265358979323846
#define FEC_ORDER_COUNT 5
#define FEC_TOTAL 7
#define FEC_KURTOSIS 8
#define FEC_CREST 9
#define FEC_RMS_VARIABILITY 10
#define FEC_ALL_FEATURES ((UINT32_C(1) << FEC_FEATURE_COUNT) - 1)

static float clamp01(float x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
static float maxf(float a, float b) { return a > b ? a : b; }
static uint32_t hash_u32(uint32_t hash, uint32_t value) {
    unsigned k;
    for (k = 0; k < 4; ++k) { hash ^= (value >> (8 * k)) & 255u; hash *= UINT32_C(16777619); }
    return hash;
}
static uint32_t hash_float(uint32_t hash, float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return hash_u32(hash, bits);
}
static uint32_t config_hash(const fec_config_t *c) {
    uint32_t h = UINT32_C(2166136261);
    h = hash_float(h, c->sensor_max_hz); h = hash_float(h, c->upper_band_min_hz);
    h = hash_float(h, c->energy_floor); h = hash_float(h, c->log_mad_floor);
    h = hash_float(h, c->z_start); h = hash_float(h, c->z_full);
    h = hash_float(h, c->support_tolerance);
    h = hash_float(h, c->relative_abnormality_min);
    h = hash_float(h, c->relative_strength_sum_min); h = hash_float(h, c->ewma_alpha);
    h = hash_u32(h, c->max_gap_ms); h = hash_u32(h, c->relative_min_windows); h = hash_u32(h, c->config_id);
    h = hash_u32(h, c->pwm_command); h = hash_u32(h, c->fg_pulses_per_rev);
    h = hash_u32(h, c->coupling_present); h = hash_u32(h, c->bearing_geometry_present);
    return hash_u32(h, c->belt_metadata_present);
}
static int config_valid(const fec_config_t *c) {
    return c && isfinite(c->sensor_max_hz) && c->sensor_max_hz > 0 &&
        isfinite(c->upper_band_min_hz) && c->upper_band_min_hz > 0 &&
        isfinite(c->energy_floor) && c->energy_floor > 0 &&
        isfinite(c->log_mad_floor) && c->log_mad_floor > 0 &&
        isfinite(c->z_start) && c->z_start >= 0 &&
        isfinite(c->z_full) && c->z_full > c->z_start &&
        isfinite(c->support_tolerance) && c->support_tolerance >= 0 && c->support_tolerance <= 0.05f &&
        isfinite(c->relative_abnormality_min) && c->relative_abnormality_min >= 0 && c->relative_abnormality_min <= 100 &&
        isfinite(c->relative_strength_sum_min) && c->relative_strength_sum_min > 0 &&
        isfinite(c->ewma_alpha) && c->ewma_alpha > 0 && c->ewma_alpha <= 1 &&
        c->fg_pulses_per_rev > 0 && c->relative_min_windows >= 3;
}
static void result_reset(fec_result_t *r) {
    unsigned k;
    memset(r, 0, sizeof(*r));
    r->status = FEC_INVALID_ARGUMENT; r->baseline_status = FEC_BASELINE_NOT_READY;
    r->abnormality_percent = NAN;
    for (k = 0; k < FEC_BAND_COUNT; ++k) {
        r->energy[k] = r->share_percent[k] = NAN;
        r->band_reason[k] = FEC_INVALID_ARGUMENT;
    }
    for (k = 0; k < FEC_FEATURE_COUNT; ++k) r->feature[k] = r->deviation[k] = NAN;
    for (k = 0; k < FEC_CANDIDATE_COUNT; ++k) {
        r->strength_percent[k] = r->relative_percent[k] = NAN;
        r->candidate_reason[k] = FEC_BASELINE_NOT_READY;
    }
}
void fec_config_default(fec_config_t *c) {
    if (!c) return;
    memset(c, 0, sizeof(*c));
    c->sensor_max_hz = 160; c->upper_band_min_hz = 80;
    c->energy_floor = 1e-12f; c->log_mad_floor = 0.20f;
    c->z_start = 2; c->z_full = 6; c->support_tolerance = 0.05f;
    c->relative_abnormality_min = 20; c->relative_strength_sum_min = 20;
    c->ewma_alpha = 0.4f; c->max_gap_ms = 500; c->relative_min_windows = 3; c->fg_pulses_per_rev = 2;
}
void fec_learner_reset(fec_learner_t *l) { if (l) memset(l, 0, sizeof(*l)); }
void fec_temporal_reset(fec_temporal_t *t) { if (t) memset(t, 0, sizeof(*t)); }
uint32_t fec_config_hash(const fec_config_t *c) { return config_valid(c) ? config_hash(c) : 0; }

static void fft(fec_workspace_t *s) {
    unsigned i, j = 0, bit, len, base, k;
    for (i = 1; i < FEC_FFT_SIZE; ++i) {
        bit = FEC_FFT_SIZE >> 1;
        while (j & bit) { j ^= bit; bit >>= 1; }
        j ^= bit;
        if (i < j) {
            float tmp = s->re[i]; s->re[i] = s->re[j]; s->re[j] = tmp;
            tmp = s->im[i]; s->im[i] = s->im[j]; s->im[j] = tmp;
        }
    }
    for (len = 2; len <= FEC_FFT_SIZE; len <<= 1) {
        float angle = (float)(-2 * FEC_PI / len);
        float step_r = cosf(angle), step_i = sinf(angle);
        for (base = 0; base < FEC_FFT_SIZE; base += len) {
            float wr = 1, wi = 0;
            for (k = 0; k < len / 2; ++k) {
                unsigned a = base + k, b = a + len / 2;
                float br = wr * s->re[b] - wi * s->im[b];
                float bi = wr * s->im[b] + wi * s->re[b];
                float next_r;
                s->re[b] = s->re[a] - br; s->im[b] = s->im[a] - bi;
                s->re[a] += br; s->im[a] += bi;
                next_r = wr * step_r - wi * step_i;
                wi = wr * step_i + wi * step_r; wr = next_r;
            }
        }
    }
}

fec_reason_t fec_extract(const fec_config_t *c, const fec_window_t *w,
                         fec_workspace_t *s, fec_result_t *r) {
    static const float orders[FEC_ORDER_COUNT] = {0.5f, 1, 1.5f, 2, 3};
    float low[FEC_ORDER_COUNT], high[FEC_ORDER_COUNT];
    unsigned i, axis, k, j, axis_count = 0, last;
    double total = 0, trusted = 0, m2 = 0, m4 = 0, peak2 = 0;
    double sub_mean = 0, sub_square = 0;
    double band_energy[FEC_BAND_COUNT] = {0};
    if (!r) return FEC_INVALID_ARGUMENT;
    result_reset(r);
    if (!config_valid(c) || !w || !s) return r->status;
    if (!w->quality_ok || w->end_ms <= w->start_ms) return r->status = FEC_BAD_QUALITY;
    if (!isfinite(w->fs_hz) || !isfinite(w->fr_hz) || w->fs_hz <= 0 || w->fr_hz <= 0)
        return r->status = FEC_NONFINITE;
    r->bin_hz = w->fs_hz / FEC_FFT_SIZE;
    r->trusted_min_hz = r->bin_hz;
    r->trusted_max_hz = fminf(c->sensor_max_hz, 0.4f * w->fs_hz);
    if (!isfinite(r->bin_hz) || r->bin_hz <= 0 || r->trusted_max_hz < r->bin_hz)
        return r->status = FEC_INVALID_ARGUMENT;
    last = (unsigned)floorf(r->trusted_max_hz / r->bin_hz);
    if (last > FEC_FFT_SIZE / 2) last = FEC_FFT_SIZE / 2;
    memset(s->power, 0, sizeof(s->power)); memset(s->radial2, 0, sizeof(s->radial2));
    for (axis = 0; axis < 3; ++axis) {
        double mean = 0;
        if (!w->axis[axis]) continue;
        r->axis_mask |= UINT32_C(1) << axis;
        ++axis_count;
        for (i = 0; i < FEC_FFT_SIZE; ++i) {
            if (!isfinite(w->axis[axis][i])) return r->status = FEC_NONFINITE;
            mean += w->axis[axis][i];
        }
        mean /= FEC_FFT_SIZE;
        for (i = 0; i < FEC_FFT_SIZE; ++i) {
            double v = w->axis[axis][i] - mean;
            double radial = s->radial2[i] + v * v;
            if (radial > FLT_MAX) return r->status = FEC_NONFINITE;
            s->radial2[i] = (float)radial;
            s->re[i] = (float)(v * (0.5 - 0.5 * cos(2 * FEC_PI * i / FEC_FFT_SIZE)));
            s->im[i] = 0;
        }
        fft(s);
        /* Periodic Hann U=3/8; one-sided mean-square power. DC excluded. */
        for (k = 1; k < FEC_FFT_BINS; ++k) {
            double p = ((double)s->re[k] * s->re[k] + (double)s->im[k] * s->im[k]) /
                       (FEC_FFT_SIZE * FEC_FFT_SIZE * 0.375);
            if (k != FEC_FFT_SIZE / 2) p *= 2;
            p += s->power[k];
            if (!isfinite(p) || p > FLT_MAX) return r->status = FEC_NONFINITE;
            s->power[k] = (float)p;
        }
    }
    if (!axis_count) return r->status = FEC_INVALID_ARGUMENT;
    for (k = 1; k < FEC_FFT_BINS; ++k) {
        total += s->power[k]; if (k <= last) trusted += s->power[k];
    }
    if (total > FLT_MAX || trusted > FLT_MAX) return r->status = FEC_NONFINITE;
    r->full_nyquist_energy = (float)total; r->trusted_energy = (float)trusted;
    r->trusted_coverage_percent = total > 0 ? (float)(100 * trusted / total) : 0;
    if (trusted <= c->energy_floor) return r->status = FEC_ZERO_ENERGY;
    for (j = 0; j < FEC_ORDER_COUNT; ++j) {
        float center = orders[j] * w->fr_hz;
        float half = maxf(2 * r->bin_hz, 0.05f * center);
        low[j] = center - half; high[j] = center + half;
        r->band_available[j] = low[j] >= r->trusted_min_hz && high[j] <= r->trusted_max_hz;
        r->band_reason[j] = r->band_available[j] ? FEC_OK : FEC_OUTSIDE_TRUSTED_BAND;
    }
    /* Inspect original eligibility rather than availability as it is changed. */
    for (j = 0; j < FEC_ORDER_COUNT; ++j) for (k = j + 1; k < FEC_ORDER_COUNT; ++k) {
        if (low[j] >= r->trusted_min_hz && high[j] <= r->trusted_max_hz &&
            low[k] >= r->trusted_min_hz && high[k] <= r->trusted_max_hz &&
            low[j] <= high[k] && low[k] <= high[j]) {
            r->band_available[j] = r->band_available[k] = 0;
            r->band_reason[j] = r->band_reason[k] = FEC_OVERLAPPING_ORDERS;
        }
    }
    for (k = 0; k < FEC_FFT_BINS; ++k) s->bin_owner[k] = -1;
    for (k = 1; k <= last; ++k) {
        float hz = k * r->bin_hz;
        int owner = -1;
        for (j = 0; j < FEC_ORDER_COUNT; ++j)
            if (r->band_available[j] && hz >= low[j] && hz <= high[j]) { owner = (int)j; break; }
        if (owner < 0) owner = hz >= c->upper_band_min_hz ? FEC_UPPER : FEC_OTHER;
        s->bin_owner[k] = (int8_t)owner;
        band_energy[owner] += s->power[k]; ++r->band_bins[owner];
    }
    for (j = 0; j < FEC_BAND_COUNT; ++j) {
        if (j >= FEC_ORDER_COUNT) {
            r->band_available[j] = r->band_bins[j] > 0;
            r->band_reason[j] = r->band_available[j] ? FEC_OK : FEC_EMPTY_BAND;
        }
        if (r->band_available[j] && !r->band_bins[j]) {
            r->band_available[j] = 0; r->band_reason[j] = FEC_EMPTY_BAND;
        }
        if (r->band_available[j]) {
            r->energy[j] = (float)band_energy[j];
            r->share_percent[j] = (float)(100 * band_energy[j] / trusted);
            r->feature[j] = maxf(r->energy[j], c->energy_floor);
            r->feature_mask |= UINT32_C(1) << j;
        }
    }
    for (i = 0; i < FEC_FFT_SIZE; ++i) {
        double v = s->radial2[i]; m2 += v; m4 += v * v; if (v > peak2) peak2 = v;
    }
    m2 /= FEC_FFT_SIZE; m4 /= FEC_FFT_SIZE;
    if (m2 <= c->energy_floor) return r->status = FEC_ZERO_ENERGY;
    /* Radial fourth moment / mean radial energy squared (single-axis sine=1.5). */
    r->feature[FEC_TOTAL] = r->trusted_energy;
    r->feature[FEC_KURTOSIS] = (float)(m4 / (m2 * m2));
    r->feature[FEC_CREST] = (float)sqrt(peak2 / m2);
    for (j = 0; j < 8; ++j) {
        double sub = 0;
        for (i = j * 64; i < (j + 1) * 64; ++i) sub += s->radial2[i];
        sub = sqrt(sub / 64); sub_mean += sub; sub_square += sub * sub;
    }
    sub_mean /= 8; sub_square /= 8;
    r->feature[FEC_RMS_VARIABILITY] = maxf((float)(sqrt(fmax(0, sub_square - sub_mean * sub_mean)) / sub_mean), 1e-8f);
    r->feature_mask |= (UINT32_C(1) << FEC_TOTAL) | (UINT32_C(1) << FEC_KURTOSIS) |
                       (UINT32_C(1) << FEC_CREST) | (UINT32_C(1) << FEC_RMS_VARIABILITY);
    r->observation_start_ms = w->start_ms; r->observation_end_ms = w->end_ms;
    return r->status = FEC_OK;
}

fec_reason_t fec_baseline_add(const fec_config_t *c, const fec_window_t *w,
                              fec_learner_t *l, fec_workspace_t *s, fec_result_t *r) {
    uint32_t h; unsigned j; fec_reason_t reason;
    if (!l) return FEC_INVALID_ARGUMENT;
    reason = fec_extract(c, w, s, r); if (reason != FEC_OK) return reason;
    if (l->count >= FEC_BASELINE_WINDOWS) return r->baseline_status = FEC_BASELINE_COMPLETE;
    h = config_hash(c);
    if (l->count) {
        if (l->config_hash != h) return r->baseline_status = FEC_CONFIG_CHANGED;
        if (l->axis_mask != r->axis_mask) return r->baseline_status = FEC_AXIS_SUPPORT_CHANGED;
        if (l->feature_mask != r->feature_mask) return r->baseline_status = FEC_BAND_SUPPORT_CHANGED;
        /* One baseline is restricted to one operating neighborhood. */
        if (w->fs_hz < l->reference_fs_hz * (1 - c->support_tolerance) ||
            w->fs_hz > l->reference_fs_hz * (1 + c->support_tolerance))
            return r->baseline_status = FEC_FS_UNSUPPORTED;
        if (w->fr_hz < l->reference_fr_hz * (1 - c->support_tolerance) ||
            w->fr_hz > l->reference_fr_hz * (1 + c->support_tolerance))
            return r->baseline_status = FEC_RPM_UNSUPPORTED;
    } else {
        l->config_hash = h; l->feature_mask = r->feature_mask;
        l->axis_mask = r->axis_mask;
        l->reference_fs_hz = w->fs_hz; l->reference_fr_hz = w->fr_hz;
        l->fs_min_hz = l->fs_max_hz = w->fs_hz; l->fr_min_hz = l->fr_max_hz = w->fr_hz;
    }
    l->fs_min_hz = fminf(l->fs_min_hz, w->fs_hz); l->fs_max_hz = maxf(l->fs_max_hz, w->fs_hz);
    l->fr_min_hz = fminf(l->fr_min_hz, w->fr_hz); l->fr_max_hz = maxf(l->fr_max_hz, w->fr_hz);
    for (j = 0; j < FEC_FEATURE_COUNT; ++j)
        l->logs[l->count][j] = r->feature_mask & (UINT32_C(1) << j) ? logf(r->feature[j]) : NAN;
    ++l->count; r->baseline_status = FEC_OK;
    return FEC_OK;
}
static float sorted_median(float *a) {
    unsigned i, j;
    for (i = 1; i < FEC_BASELINE_WINDOWS; ++i) {
        float v = a[i]; j = i;
        while (j && a[j - 1] > v) { a[j] = a[j - 1]; --j; } a[j] = v;
    }
    return (a[FEC_BASELINE_WINDOWS / 2 - 1] + a[FEC_BASELINE_WINDOWS / 2]) * 0.5f;
}
fec_reason_t fec_baseline_finish(const fec_config_t *c, fec_learner_t *l, fec_baseline_t *b) {
    unsigned i, j; uint32_t generation;
    if (!config_valid(c) || !l || !b) return FEC_INVALID_ARGUMENT;
    if (l->count != FEC_BASELINE_WINDOWS) return FEC_BASELINE_NOT_READY;
    if (l->config_hash != config_hash(c)) return FEC_CONFIG_CHANGED;
    memset(b, 0, sizeof(*b));
    b->magic = FEC_BASELINE_MAGIC; b->abi_version = FEC_ABI_VERSION;
    b->config_hash = l->config_hash; b->count = l->count; b->feature_mask = l->feature_mask; b->axis_mask = l->axis_mask;
    b->fs_min_hz = l->fs_min_hz; b->fs_max_hz = l->fs_max_hz;
    b->fr_min_hz = l->fr_min_hz; b->fr_max_hz = l->fr_max_hz;
    generation = hash_u32(b->config_hash, b->count);
    generation = hash_u32(generation, b->feature_mask);
    generation = hash_u32(generation, b->axis_mask);
    generation = hash_float(generation, b->fs_min_hz); generation = hash_float(generation, b->fs_max_hz);
    generation = hash_float(generation, b->fr_min_hz); generation = hash_float(generation, b->fr_max_hz);
    for (j = 0; j < FEC_FEATURE_COUNT; ++j) {
        if (!(b->feature_mask & (UINT32_C(1) << j))) {
            b->log_median[j] = b->log_scale[j] = NAN; continue;
        }
        for (i = 0; i < FEC_BASELINE_WINDOWS; ++i) l->scratch[i] = l->logs[i][j];
        b->log_median[j] = sorted_median(l->scratch);
        for (i = 0; i < FEC_BASELINE_WINDOWS; ++i)
            l->scratch[i] = fabsf(l->logs[i][j] - b->log_median[j]);
        b->log_scale[j] = maxf(1.4826f * sorted_median(l->scratch), c->log_mad_floor);
        generation = hash_float(generation, b->log_median[j]); generation = hash_float(generation, b->log_scale[j]);
    }
    b->generation = generation;
    return FEC_OK;
}

fec_reason_t fec_baseline_validate(const fec_config_t *c, const fec_baseline_t *b) {
    unsigned j;
    uint32_t generation;
    if (!config_valid(c)) return FEC_INVALID_ARGUMENT;
    if (!b || b->magic != FEC_BASELINE_MAGIC || b->abi_version != FEC_ABI_VERSION ||
        b->count != FEC_BASELINE_WINDOWS || !b->axis_mask || (b->axis_mask & ~UINT32_C(7)) || (b->feature_mask & ~FEC_ALL_FEATURES) ||
        (b->feature_mask & (UINT32_C(15) << FEC_TOTAL)) != (UINT32_C(15) << FEC_TOTAL)) return FEC_BASELINE_NOT_READY;
    if (b->config_hash != config_hash(c)) return FEC_CONFIG_CHANGED;
    if (!isfinite(b->fs_min_hz) || !isfinite(b->fs_max_hz) || b->fs_min_hz <= 0 || b->fs_max_hz < b->fs_min_hz ||
        !isfinite(b->fr_min_hz) || !isfinite(b->fr_max_hz) || b->fr_min_hz <= 0 || b->fr_max_hz < b->fr_min_hz)
        return FEC_BASELINE_NOT_READY;
    for (j = 0; j < FEC_FEATURE_COUNT; ++j) if (b->feature_mask & (UINT32_C(1) << j))
        if (!isfinite(b->log_median[j]) || !isfinite(b->log_scale[j]) || b->log_scale[j] < c->log_mad_floor)
            return FEC_BASELINE_NOT_READY;
    generation = hash_u32(b->config_hash, b->count);
    generation = hash_u32(generation, b->feature_mask);
    generation = hash_u32(generation, b->axis_mask);
    generation = hash_float(generation, b->fs_min_hz); generation = hash_float(generation, b->fs_max_hz);
    generation = hash_float(generation, b->fr_min_hz); generation = hash_float(generation, b->fr_max_hz);
    for (j = 0; j < FEC_FEATURE_COUNT; ++j) if (b->feature_mask & (UINT32_C(1) << j)) {
        generation = hash_float(generation, b->log_median[j]); generation = hash_float(generation, b->log_scale[j]);
    }
    if (generation != b->generation) return FEC_BASELINE_NOT_READY;
    return FEC_OK;
}
static fec_reason_t baseline_check(const fec_config_t *c, const fec_window_t *w,
                                    const fec_baseline_t *b, const fec_result_t *r) {
    fec_reason_t reason = fec_baseline_validate(c, b);
    if (reason != FEC_OK) return reason;
    if (b->axis_mask != r->axis_mask) return FEC_AXIS_SUPPORT_CHANGED;
    if (w->fs_hz < b->fs_min_hz * (1 - c->support_tolerance) ||
        w->fs_hz > b->fs_max_hz * (1 + c->support_tolerance)) return FEC_FS_UNSUPPORTED;
    if (w->fr_hz < b->fr_min_hz * (1 - c->support_tolerance) ||
        w->fr_hz > b->fr_max_hz * (1 + c->support_tolerance)) return FEC_RPM_UNSUPPORTED;
    if (b->feature_mask != r->feature_mask) return FEC_BAND_SUPPORT_CHANGED;
    return FEC_OK;
}
static int external_valid(const fec_external_evidence_t *e, uint32_t sources, uint32_t n, int all) {
    return e->quality_ok && isfinite(e->strength) && e->strength >= 0 && e->strength <= 1 &&
        e->observations >= n && (all ? (e->sources & sources) == sources : (e->sources & sources) != 0);
}
static void candidate_unavailable(fec_result_t *r, unsigned c, fec_reason_t reason) {
    r->candidate_available[c] = 0; r->candidate_reason[c] = reason;
    r->strength_percent[c] = r->relative_percent[c] = NAN;
}
static float deviation(const fec_result_t *r, unsigned j) {
    return r->feature_mask & (UINT32_C(1) << j) ? r->deviation[j] : 0;
}

fec_reason_t fec_evaluate(const fec_config_t *c, const fec_window_t *w,
                          const fec_baseline_t *b, fec_temporal_t *t,
                          fec_workspace_t *s, fec_result_t *r) {
    unsigned j; float spectral = 0, shape, irregularity, physical = 0, abnormality, sum = 0;
    float dominance1, harmonics_share = 0, order_growth = 0;
    const fec_external_evidence_t *phase, *mis, *bearing, *belt;
    fec_reason_t reason = fec_extract(c, w, s, r);
    if (reason != FEC_OK) { fec_temporal_reset(t); return reason; }
    reason = baseline_check(c, w, b, r); r->baseline_status = reason;
    if (reason != FEC_OK) {
        for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) candidate_unavailable(r, j, reason);
        fec_temporal_reset(t); return reason;
    }
    for (j = 0; j < FEC_FEATURE_COUNT; ++j) if (r->feature_mask & (UINT32_C(1) << j)) {
        float z = (logf(r->feature[j]) - b->log_median[j]) / b->log_scale[j];
        r->deviation[j] = clamp01((z - c->z_start) / (c->z_full - c->z_start));
        if (j <= FEC_TOTAL) spectral = maxf(spectral, r->deviation[j]);
    }
    shape = maxf(deviation(r, FEC_KURTOSIS), deviation(r, FEC_CREST));
    irregularity = maxf(shape, deviation(r, FEC_RMS_VARIABILITY));
    phase = &w->external[FEC_IMBALANCE]; mis = &w->external[FEC_MISALIGNMENT];
    bearing = &w->external[FEC_BEARING]; belt = &w->external[FEC_BELT];
    for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) {
        r->candidate_available[j] = 1; r->candidate_reason[j] = FEC_OK; r->strength_percent[j] = 0;
    }
    if (!r->band_available[FEC_ONE]) candidate_unavailable(r, FEC_IMBALANCE, r->band_reason[FEC_ONE]);
    else if (!external_valid(phase, FEC_SOURCE_FG_PHASE, 5, 1))
        candidate_unavailable(r, FEC_IMBALANCE, phase->sources & FEC_SOURCE_FG_PHASE ? FEC_EXTERNAL_BAD_QUALITY : FEC_MISSING_FG_PHASE);
    if (!r->band_available[FEC_HALF] && !r->band_available[FEC_ONE_HALF] &&
        !r->band_available[FEC_TWO] && !r->band_available[FEC_THREE])
        candidate_unavailable(r, FEC_LOOSENESS, FEC_BAND_SUPPORT_CHANGED);
    if (!c->coupling_present) candidate_unavailable(r, FEC_MISALIGNMENT, FEC_MISSING_COUPLING);
    else if (!external_valid(mis, FEC_SOURCE_DIRECTION | FEC_SOURCE_SECOND_LOCATION, 1, 0))
        candidate_unavailable(r, FEC_MISALIGNMENT, mis->sources ? FEC_EXTERNAL_BAD_QUALITY : FEC_MISSING_DIRECTION_OR_LOCATION);
    else if (!r->band_available[FEC_ONE] && !r->band_available[FEC_TWO] && !r->band_available[FEC_THREE])
        candidate_unavailable(r, FEC_MISALIGNMENT, FEC_BAND_SUPPORT_CHANGED);
    else physical = maxf(physical, mis->strength);
    if (!c->bearing_geometry_present) candidate_unavailable(r, FEC_BEARING, FEC_MISSING_BEARING_METADATA);
    else if (!external_valid(bearing, FEC_SOURCE_REAL_ENVELOPE | FEC_SOURCE_BEARING_GEOMETRY, 1, 1))
        candidate_unavailable(r, FEC_BEARING, bearing->sources ? FEC_EXTERNAL_BAD_QUALITY : FEC_MISSING_ENVELOPE_OR_GEOMETRY);
    else physical = maxf(physical, bearing->strength);
    if (!c->belt_metadata_present) candidate_unavailable(r, FEC_BELT, FEC_MISSING_BELT_METADATA);
    else if (!external_valid(belt, FEC_SOURCE_TRANSMISSION | FEC_SOURCE_TENSION | FEC_SOURCE_BELT_CYCLE, 1, 0))
        candidate_unavailable(r, FEC_BELT, belt->sources ? FEC_EXTERNAL_BAD_QUALITY : FEC_MISSING_TRANSMISSION_EVIDENCE);
    else physical = maxf(physical, belt->strength);
    /* Independent groups: spectrum, irregularity, additional physical channels.
     * Within groups correlated features contribute only their maximum. This is a
     * bounded evidence rule, not an independent-events probability model. */
    abnormality = 1 - (1 - spectral) * (1 - 0.5f * irregularity) * (1 - 0.75f * physical);
    r->abnormality_percent = 100 * clamp01(abnormality);
    dominance1 = r->band_available[FEC_ONE] ? clamp01(r->share_percent[FEC_ONE] / 70) : 0;
    if (r->candidate_available[FEC_IMBALANCE])
        r->strength_percent[FEC_IMBALANCE] = r->abnormality_percent *
            sqrtf(deviation(r, FEC_ONE) * dominance1) * phase->strength;
    for (j = 0; j < FEC_ORDER_COUNT; ++j) if (j != FEC_ONE && r->band_available[j]) {
        order_growth = maxf(order_growth, r->deviation[j]); harmonics_share += r->share_percent[j];
    }
    if (r->candidate_available[FEC_LOOSENESS])
        r->strength_percent[FEC_LOOSENESS] = r->abnormality_percent *
            sqrtf(order_growth * clamp01(harmonics_share / 50) * irregularity);
    if (r->candidate_available[FEC_MISALIGNMENT])
        r->strength_percent[FEC_MISALIGNMENT] = r->abnormality_percent *
            sqrtf(maxf(deviation(r, FEC_ONE), maxf(deviation(r, FEC_TWO), deviation(r, FEC_THREE))) * mis->strength);
    if (r->candidate_available[FEC_BEARING]) r->strength_percent[FEC_BEARING] = r->abnormality_percent * bearing->strength;
    if (r->candidate_available[FEC_BELT]) r->strength_percent[FEC_BELT] = r->abnormality_percent * belt->strength;
    if (t) {
        int reset = !t->initialized || t->config_hash != b->config_hash || t->baseline_generation != b->generation ||
            w->start_ms < t->last_end_ms || w->start_ms - t->last_end_ms > c->max_gap_ms;
        r->temporal_reset = (uint8_t)reset;
        if (reset) {
            fec_temporal_reset(t); t->initialized = 1; t->config_hash = b->config_hash;
            t->baseline_generation = b->generation; t->first_start_ms = w->start_ms;
            t->abnormality = r->abnormality_percent;
        } else t->abnormality += c->ewma_alpha * (r->abnormality_percent - t->abnormality);
        if (t->observation_count < UINT32_MAX) ++t->observation_count;
        t->last_end_ms = w->end_ms;
        for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) {
            if (r->candidate_available[j]) {
                if (!t->candidate_count[j]) t->strength[j] = r->strength_percent[j];
                else t->strength[j] += c->ewma_alpha * (r->strength_percent[j] - t->strength[j]);
                if (t->candidate_count[j] < UINT32_MAX) ++t->candidate_count[j];
                r->strength_percent[j] = t->strength[j];
            } else { t->candidate_count[j] = 0; t->strength[j] = 0; }
            r->candidate_observation_count[j] = t->candidate_count[j];
        }
        r->abnormality_percent = t->abnormality; r->observation_count = t->observation_count;
        r->observation_start_ms = t->first_start_ms; r->observation_end_ms = t->last_end_ms;
    } else {
        r->observation_count = 1;
        for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) r->candidate_observation_count[j] = r->candidate_available[j] ? 1 : 0;
    }
    for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) if (r->candidate_available[j]) sum += r->strength_percent[j];
    r->relative_visible = r->abnormality_percent >= c->relative_abnormality_min && sum >= c->relative_strength_sum_min;
    if (!t || r->observation_count < c->relative_min_windows) r->relative_visible = 0;
    for (j = 0; j < FEC_CANDIDATE_COUNT; ++j)
        if (r->candidate_available[j] && r->candidate_observation_count[j] < c->relative_min_windows) r->relative_visible = 0;
    if (r->relative_visible)
        for (j = 0; j < FEC_CANDIDATE_COUNT; ++j) if (r->candidate_available[j])
            r->relative_percent[j] = 100 * r->strength_percent[j] / sum;
    return FEC_OK;
}

const char *fec_reason_name(fec_reason_t reason) {
    static const char *const names[] = {"ok", "invalid_argument", "bad_quality", "nonfinite", "zero_energy",
        "outside_trusted_band", "overlapping_orders", "empty_band", "baseline_not_ready", "config_changed",
        "fs_unsupported", "rpm_unsupported", "band_support_changed", "missing_fg_phase", "missing_coupling",
        "missing_direction_or_location", "missing_bearing_metadata", "missing_envelope_or_geometry",
        "missing_belt_metadata", "missing_transmission_evidence", "external_bad_quality", "baseline_complete", "axis_support_changed"};
    return (unsigned)reason < sizeof(names) / sizeof(names[0]) ? names[reason] : "unknown";
}
const char *fec_band_name(fec_band_t band) {
    static const char *const names[] = {"0.5X", "1X", "1.5X", "2X", "3X", "sensor_upper", "other"};
    return (unsigned)band < FEC_BAND_COUNT ? names[band] : "unknown";
}
const char *fec_candidate_name(fec_candidate_t candidate) {
    static const char *const names[] = {"imbalance", "looseness", "misalignment", "bearing", "belt"};
    return (unsigned)candidate < FEC_CANDIDATE_COUNT ? names[candidate] : "unknown";
}
const char *fec_feature_name(fec_feature_t feature) {
    static const char *const names[] = {"energy_0_5x", "energy_1x", "energy_1_5x", "energy_2x",
        "energy_3x", "energy_sensor_upper", "energy_other", "energy_trusted_total",
        "radial_kurtosis", "crest", "subwindow_rms_cv"};
    return (unsigned)feature < FEC_FEATURE_COUNT ? names[feature] : "unknown";
}
