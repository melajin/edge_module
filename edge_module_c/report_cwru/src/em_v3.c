#include "em_v3.h"
#include <math.h>
#include <string.h>

static bool valid_number(float x) { return isfinite(x) != 0; }

bool em_v3_init(em_v3_t *v, float amplitude_ratio, float phase_concentration)
{
    if (!v || !valid_number(amplitude_ratio) || amplitude_ratio <= 0 ||
        !valid_number(phase_concentration) || phase_concentration < 0 ||
        phase_concentration > 1) return false;
    memset(v, 0, sizeof(*v));
    v->imbalance_amplitude_ratio = amplitude_ratio;
    v->imbalance_phase_concentration = phase_concentration;
    return true;
}

void em_v3_reset(em_v3_t *v)
{
    if (!v) return;
    memset(&v->bearing, 0, sizeof(v->bearing));
    memset(&v->misalignment, 0, sizeof(v->misalignment));
    memset(&v->belt, 0, sizeof(v->belt));
    memset(&v->imbalance, 0, sizeof(v->imbalance));
}

static void push(em_v3_history_t *h, bool positive)
{
    if (h->count == EM_V3_WINDOW) h->positive -= h->bits[h->next];
    else h->count++;
    h->bits[h->next] = positive ? 1 : 0;
    h->positive += h->bits[h->next];
    h->next = (uint8_t)((h->next + 1) % EM_V3_WINDOW);
}

static em_v3_fault_result_t instant_result(em_v3_history_t *h, em_v3_instant_t in)
{
    em_v3_fault_result_t r;
    if (in.valid) push(h, in.flag);
    r.status = (!in.valid || h->count < EM_V3_WINDOW) ? EM_V3_UNAVAILABLE :
        (h->positive >= EM_V3_REQUIRED ? EM_V3_CONFIRMED : EM_V3_NORMAL);
    r.votes_positive = h->positive;
    r.votes_valid = h->count;
    r.instant_valid = in.valid;
    r.instant_flag = in.valid && in.flag;
    return r;
}

void em_v3_unavailable(em_v3_result_t *out)
{
    if (!out) return;
    memset(out, 0, sizeof(*out));
    out->imbalance_auto_confirmation_allowed = false;
}

bool em_v3_update(em_v3_t *v, const em_v3_input_t *in, em_v3_result_t *out)
{
    if (!v || !in || !out) return false;
    const em_v3_imbalance_t *im = &in->imbalance;
    if ((im->amplitude_present && (!valid_number(im->amplitude_ratio_1x) ||
                                  im->amplitude_ratio_1x < 0)) ||
        (im->phase_present && (!valid_number(im->phase_concentration) ||
                               im->phase_concentration < 0 ||
                               im->phase_concentration > 1))) return false;
    out->bearing = instant_result(&v->bearing, in->bearing);
    out->misalignment = instant_result(&v->misalignment, in->misalignment);
    out->belt = instant_result(&v->belt, in->belt);
    bool valid = im->rpm_available && im->baseline_available &&
                 im->amplitude_present && im->phase_present;
    bool positive = valid && im->amplitude_ratio_1x >= v->imbalance_amplitude_ratio &&
                    im->phase_concentration >= v->imbalance_phase_concentration;
    if (valid) push(&v->imbalance, positive);
    out->imbalance.votes_positive = v->imbalance.positive;
    out->imbalance.votes_valid = v->imbalance.count;
    out->imbalance.instant_valid = valid;
    out->imbalance.instant_flag = positive;
    out->imbalance.status = (!valid || v->imbalance.count < EM_V3_WINDOW) ?
        EM_V3_UNAVAILABLE : (v->imbalance.positive < EM_V3_REQUIRED ?
        EM_V3_NORMAL : (out->misalignment.status == EM_V3_CONFIRMED ?
        EM_V3_INSPECTION_REQUIRED : EM_V3_SUSPECTED));
    out->imbalance_auto_confirmation_allowed = false;
    return true;
}

const char *em_v3_status_name(em_v3_status_t status)
{
    switch (status) {
    case EM_V3_NORMAL: return "normal";
    case EM_V3_CONFIRMED: return "confirmed";
    case EM_V3_SUSPECTED: return "suspected";
    case EM_V3_INSPECTION_REQUIRED: return "inspection_required";
    default: return "unavailable";
    }
}
