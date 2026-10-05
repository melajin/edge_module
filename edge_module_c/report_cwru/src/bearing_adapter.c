#include "bearing_adapter.h"
#include <math.h>
#include <string.h>
#include <limits.h>
void bearing_init(bearing_adapter *s) {
    if (!s) return;
    memset(s, 0, sizeof(*s));
    em_v3_init(&s->policy, 1.25f, 0.95f);
    s->output.class_index = -1;
}
void bearing_reset(bearing_adapter *s) { bearing_init(s); }
static int reject(bearing_adapter *s, int reason) {
    bearing_reset(s);
    return reason;
}
void bearing_expire(bearing_adapter *s, uint32_t now) {
    if (s && s->have_window && (uint32_t)(now-s->last_update_ms)>BEARING_FRESHNESS_MS)
        bearing_reset(s);
}
void bearing_merge(const bearing_adapter *s, em_v3_result_t *out) {
    if (s && out) out->bearing = s->output.bearing;
}
int bearing_submit(bearing_adapter *s, const bearing_input *in,
                   const double *x, size_t n, em_cwru_scratch *scratch, uint32_t now) {
    int restarted=0;
    bearing_output next;
    em_v3_input_t evidence;
    em_v3_result_t result;
    if (!s) return -1;
    if (!in || !x || !scratch || n!=EM_CWRU_N || in->sample_rate_hz!=12000.0 ||
        !isfinite(in->rated_rpm) || in->rated_rpm<=0 || in->rated_rpm/60>=6000 ||
        in->dropped_samples) return reject(s,-1);
    /* A repeated/out-of-order window is never a second vote. New sessions
     * and forward gaps start fresh, so neither can inherit old confirmations. */
    if (s->have_window) {
        if (in->session!=s->session) restarted=1;
        else if (in->sequence<=s->sequence || in->sample_start<=s->sample_start)
            return reject(s,-2);
        else if (in->rated_rpm!=s->rated_rpm || s->sequence==UINT64_MAX || s->sample_start>UINT64_MAX-BEARING_STRIDE ||
                 in->sequence!=s->sequence+1 || in->sample_start!=s->sample_start+BEARING_STRIDE ||
                 (uint32_t)(now-s->last_update_ms)>BEARING_FRESHNESS_MS) restarted=1;
    }
    if (em_cwru_features(x,n,in->sample_rate_hz,in->rated_rpm,scratch,next.features) ||
        em_cwru_predict(next.features,&next.class_index,next.scores)) return reject(s,-3);
    if (restarted) bearing_reset(s);
    memset(&evidence,0,sizeof(evidence));
    evidence.bearing.valid=true;
    evidence.bearing.flag=(next.class_index!=2);
    /* Do not call em_cwru_vote_update here: em_v3 owns the sole 4/5 policy. */
    if (!em_v3_update(&s->policy,&evidence,&result)) return reject(s,-4);
    next.bearing=result.bearing;
    s->output=next;
    s->session=in->session; s->sequence=in->sequence; s->sample_start=in->sample_start;
    s->rated_rpm=in->rated_rpm;
    s->last_update_ms=now; s->have_window=true;
    return restarted;
}
