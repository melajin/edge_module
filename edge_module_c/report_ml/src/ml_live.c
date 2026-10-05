#include "ml_live.h"
#include "ml_reference.h"
#include <math.h>
#include <string.h>
static double wave[400],features[101];
void ml_live_run(const v3_sample_t *samples,unsigned count,int axis,
 const v3_signal_result_t *signal,int acquisition_ok,ml_live_result_t *out){
 memset(out,0,sizeof(*out));out->label=-1;out->fs_hz=out->rpm=NAN;
 for(int i=0;i<4;i++)out->scores[i]=NAN;
 out->reason="invalid_acquisition";
 if(!acquisition_ok||!signal||signal->reason!=V3_SIGNAL_OK||count!=512||!samples||axis<0||axis>2)return;
 out->fs_hz=signal->sample_rate_hz;out->rpm=signal->rpm;
 if(!isfinite(out->fs_hz)||out->fs_hz<380||out->fs_hz>420||!isfinite(out->rpm)||out->rpm<=0){out->reason="invalid_fs_or_rpm";return;}
 if(!isfinite(signal->fg_max_deviation_pct)||signal->fg_max_deviation_pct>5){out->reason="rotation_spread";return;}
 uint32_t base=samples[0].time_us;double end=(uint32_t)(samples[511].time_us-base)/1e6;
 for(unsigned j=0;j<512;j++){
  if(!isfinite(samples[j].g[axis])||fabs(samples[j].g[axis])>=3.95){out->reason="invalid_sensor_sample";return;}
  if(j&&fabs((uint32_t)(samples[j].time_us-samples[j-1].time_us)-1e6/out->fs_hz)>0.05*1e6/out->fs_hz){out->reason="invalid_time_axis";return;}
 }
 /* Adaptation deliberately differs from training resample_poly: interpolation
  * of actual conversion timestamps at 400 Hz. Report this domain shift. */
 double start=end-399./400.;if(start<0){out->reason="insufficient_one_second";return;}
 unsigned j=0;
 for(unsigned k=0;k<400;k++){
  double t=start+k/400.;while(j<510&&(uint32_t)(samples[j+1].time_us-base)/1e6<t)j++;
  double lo=(uint32_t)(samples[j].time_us-base)/1e6,hi=(uint32_t)(samples[j+1].time_us-base)/1e6;
  if(hi<=lo){out->reason="invalid_time_axis";return;}
  double a=(t-lo)/(hi-lo);wave[k]=samples[j].g[axis]+a*(samples[j+1].g[axis]-samples[j].g[axis]);
 }
 double nyquist=fmin(200.,out->fs_hz/2.);
 for(int k=1;k<=18;k++)if((k*.5+.15)*out->rpm/60.<nyquist)out->order_mask|=1u<<(k-1);
 if(!ml_reference_extract(wave,out->rpm,features)){out->reason="zero_or_nonfinite_features";return;}
 out->label=ml_reference_predict(features,out->scores);
 out->reason=out->label>=0?"experimental_domain_mismatch":"nonfinite_inference";
}
