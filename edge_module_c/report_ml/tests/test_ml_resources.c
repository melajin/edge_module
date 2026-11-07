#include "ml_live.h"
#include "ml_reference.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned allocation_calls;
static int fail_allocation;
static size_t allocation_bytes;
/* Only ml_reference.c is compiled with calloc redirected to this function. */
void *test_calloc(size_t count,size_t bytes){
 allocation_calls++;allocation_bytes=count*bytes;
 return fail_allocation?NULL:calloc(count,bytes);
}
static void make_signal(v3_sample_t samples[512],v3_signal_result_t *signal){
 memset(samples,0,sizeof(v3_sample_t)*512);
 memset(signal,0,sizeof(*signal));
 signal->reason=V3_SIGNAL_OK;signal->sample_rate_hz=400;signal->rpm=1238;
 for(unsigned j=0;j<512;j++){
  samples[j].time_us=100000+j*2500;
  samples[j].g[0]=(float)(sin(2*3.14159265358979323846*20*j/400.)
                 +.3*sin(2*3.14159265358979323846*40*j/400.));
 }
}
#ifndef ML_NUMERIC_ONLY
static void assert_unavailable(const ml_live_result_t *result,const char *reason){
 assert(result->label==-1);assert(strcmp(result->reason,reason)==0);
 for(int i=0;i<4;i++)assert(isnan(result->scores[i]));
 assert(result->order_mask==((1u<<18)-1));
 assert(result->fs_hz==400&&result->rpm==1238);
}
#endif
int main(void){
 v3_sample_t samples[512];v3_signal_result_t signal;ml_live_result_t result;
 double input[400],features[101],scores[4];
 make_signal(samples,&signal);
 for(int i=0;i<400;i++)input[i]=sin(2*3.14159265358979323846*20*i/400.)
                             +.3*sin(2*3.14159265358979323846*40*i/400.);
#ifndef ML_NUMERIC_ONLY
 assert(ml_reference_resource_state()==ML_REFERENCE_UNINITIALIZED);
 assert(ml_reference_workspace_bytes()>0);assert(allocation_calls==0);
 assert(!ml_reference_extract(input,1238,features));
 ml_live_run(samples,512,0,&signal,1,&result);
 assert_unavailable(&result,"ml_workspace_uninitialized");assert(allocation_calls==0);
 fail_allocation=1;assert(!ml_reference_init());assert(allocation_calls==1);
 assert(allocation_bytes==ml_reference_workspace_bytes());
 assert(ml_reference_resource_state()==ML_REFERENCE_ALLOCATION_FAILED);
 ml_live_run(samples,512,0,&signal,1,&result);
 assert_unavailable(&result,"ml_workspace_allocation_failed");assert(allocation_calls==1);
 assert(!ml_reference_init());assert(allocation_calls==2);
 fail_allocation=0;assert(ml_reference_init());assert(allocation_calls==3);
 assert(ml_reference_resource_state()==ML_REFERENCE_READY);
 fail_allocation=1;
 for(int i=0;i<10;i++)assert(ml_reference_init());
 assert(allocation_calls==3);
#else
 assert(ml_reference_init());
#endif
 assert(ml_reference_extract(input,1238,features));
 int label=ml_reference_predict(features,scores);assert(label>=0);
 ml_live_run(samples,512,0,&signal,1,&result);
 assert(result.label>=0&&strcmp(result.reason,"experimental_domain_mismatch")==0);
#ifdef ML_NUMERIC_ONLY
 /* Hex doubles preserve every bit, without Windows stdout binary translation. */
 for(int i=0;i<101;i++)printf("%a\n",features[i]);
 for(int i=0;i<4;i++)printf("%a\n",scores[i]);
 printf("%d %d %u\n",label,result.label,result.order_mask);
 for(int i=0;i<4;i++)printf("%a\n",result.scores[i]);
#else
 assert(allocation_calls==3);
 unsigned mask=result.order_mask;
 for(unsigned j=0;j<512;j++)samples[j].g[0]=0;
 ml_live_run(samples,512,0,&signal,1,&result);
 assert_unavailable(&result,"zero_or_nonfinite_features");assert(result.order_mask==mask);
 samples[0].g[0]=NAN;
 ml_live_run(samples,512,0,&signal,1,&result);
 assert(result.label==-1&&strcmp(result.reason,"invalid_sensor_sample")==0);
 assert(result.order_mask==0);assert(allocation_calls==3);
 printf("resource lifecycle passed; workspace bytes=%zu; allocations=%u\n",
        ml_reference_workspace_bytes(),allocation_calls);
#endif
 return 0;
}
