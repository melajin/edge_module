#include "bearing_adapter.h"
#include <string.h>
static bearing_adapter state;
static em_cwru_scratch scratch;
static bearing_adapter alias_state;
void test_reset(void) { bearing_init(&state); }
int test_submit(const bearing_input *input,const double *raw,size_t count,uint32_t now) {
    return bearing_submit(&state,input,raw,count,&scratch,now);
}
const bearing_output *test_output(void) { return &state.output; }
int test_alias(const bearing_input *input,const double *raw,uint32_t now) {
    memcpy(scratch.imag,raw,sizeof(scratch.imag));
    return bearing_submit(&state,input,scratch.imag,EM_CWRU_N,&scratch,now);
}
const bearing_output *test_alias_once(const bearing_input *input,const double *raw) {
    bearing_init(&alias_state);
    memcpy(scratch.imag,raw,sizeof(scratch.imag));
    if(bearing_submit(&alias_state,input,scratch.imag,EM_CWRU_N,&scratch,0)<0) return 0;
    return &alias_state.output;
}
void test_expire(uint32_t now) { bearing_expire(&state,now); }
int test_low_updates(unsigned count) {
    em_v3_t low;
    em_v3_result_t result;
    em_v3_input_t evidence={0};
    em_v3_init(&low,1.25f,.95f);
    evidence.imbalance.rpm_available=true;
    evidence.imbalance.baseline_available=true;
    evidence.imbalance.amplitude_present=true;
    evidence.imbalance.phase_present=true;
    evidence.imbalance.amplitude_ratio_1x=2;
    evidence.imbalance.phase_concentration=1;
    for(unsigned i=0;i<count;i++) {
        em_v3_update(&low,&evidence,&result);
        bearing_merge(&state,&result);
        if(result.bearing.votes_valid!=state.output.bearing.votes_valid ||
           result.bearing.votes_positive!=state.output.bearing.votes_positive ||
           result.bearing.status!=state.output.bearing.status) return -1;
    }
    em_v3_unavailable(&result);
    bearing_merge(&state,&result);
    return result.bearing.status==state.output.bearing.status &&
           result.imbalance.status==EM_V3_UNAVAILABLE ? 0 : -1;
}
