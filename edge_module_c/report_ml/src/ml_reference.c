#include "ml_reference.h"
#include "ml_model_generated.h"
#include "ml_dsp_tables.h"
#include <math.h>
#include <string.h>
#include <stdlib.h>
#define N 400
#define PI 3.14159265358979323846
#define EPS 1e-20
const char *const ml_reference_classes[4] = {"imbalance","mechanical_looseness","misalignment","normal"};
/* Caller serializes access: fixed workspace avoids ESP32 task stack exhaustion. */
typedef struct { double x[N],re[N],im[N],env[N],p[201],ep[201],ac[25],matrix[24][25],windowed[N],stage_re[N],stage_im[N],analytic_im[N]; } ml_workspace_t;
static ml_workspace_t *scratch;
static ml_reference_resource_state_t resource_state=ML_REFERENCE_UNINITIALIZED;
int ml_reference_init(void){
 if(scratch)return 1;
 scratch=(ml_workspace_t *)calloc(1,sizeof(*scratch));
 resource_state=scratch?ML_REFERENCE_READY:ML_REFERENCE_ALLOCATION_FAILED;
 return scratch!=0;
}
ml_reference_resource_state_t ml_reference_resource_state(void){return resource_state;}
size_t ml_reference_workspace_bytes(void){return sizeof(ml_workspace_t);}
#define x (scratch->x)
#define re (scratch->re)
#define im (scratch->im)
#define env (scratch->env)
#define p (scratch->p)
#define ep (scratch->ep)
#define ac (scratch->ac)
#define matrix (scratch->matrix)
#define windowed (scratch->windowed)
#define stage_re (scratch->stage_re)
#define stage_im (scratch->stage_im)
#define analytic_im (scratch->analytic_im)
static double maxd(double a,double b){return a>b?a:b;}
static double lg(double v){return log(maxd(v,EPS));}
/* Cooley-Tukey 20x20 DFT: 16000 complex accumulations rather than 160000. */
static void dft(const double *v,const double *vi){
 for(int n1=0;n1<20;n1++)for(int k2=0;k2<20;k2++){
  double r=0,i=0;
  for(int n2=0;n2<20;n2++){
   int j=n1+20*n2,angle=(k2*n2%20)*20;double c=ML_COS[angle],si=ML_SIN[angle],vr=v[j];
   if(vi){double vv=vi[j];r+=vr*c+vv*si;i+=vv*c-vr*si;}else{r+=vr*c;i-=vr*si;}
  }
  int angle=n1*k2;double c=ML_COS[angle],si=ML_SIN[angle];stage_re[n1*20+k2]=r*c+i*si;stage_im[n1*20+k2]=i*c-r*si;
 }
 for(int k1=0;k1<20;k1++)for(int k2=0;k2<20;k2++){
  double r=0,i=0;
  for(int n1=0;n1<20;n1++){
   int j=n1*20+k2,angle=(k1*n1%20)*20;double c=ML_COS[angle],si=ML_SIN[angle],vr=stage_re[j],vv=stage_im[j];r+=vr*c+vv*si;i+=vv*c-vr*si;
  }
  re[k2+20*k1]=r;im[k2+20*k1]=i;
 }
}
static void psd(const double *v,double *out){
 double mean=0;for(int j=0;j<N;j++)mean+=v[j]/N;
 /* Separate local windowed input; DFT overwrites only frequency scratch. */
 for(int j=0;j<N;j++)windowed[j]=(v[j]-mean)*ML_HANN[j];
 dft(windowed,0);
 for(int k=0;k<=N/2;k++)out[k]=(re[k]*re[k]+im[k]*im[k])/(200.*200.)*((k==0||k==N/2)?1:2);
}
static double energy(const double *v,double lo,double hi){double a=0;for(int k=0;k<=N/2;k++)if(k>=lo&&k<hi)a+=v[k];return a;}
int ml_reference_extract(const double samples[400],double rpm,double f[101]){
 if(!scratch||!isfinite(rpm)||rpm<=0)return 0;
 double mean=0;for(int i=0;i<N;i++){if(!isfinite(samples[i]))return 0;mean+=samples[i]/N;}
 double var=0,a3=0,a4=0,absmean=0,peak=0;
 for(int i=0;i<N;i++){x[i]=samples[i]-mean;double q=x[i]*x[i];var+=q/N;a3+=q*x[i]/N;a4+=q*q/N;absmean+=fabs(x[i])/N;peak=maxd(peak,fabs(x[i]));}
 if(var<=1e-20)return 0;
 double rms=sqrt(var);psd(x,p);double total=maxd(energy(p,0,201),EPS),fr=rpm/60.;int n=0;
 f[n++]=lg(rms);for(int k=1;k<=3;k++)f[n++]=lg(energy(p,k*fr-7,k*fr+7)/total);
 f[n++]=lg(energy(p,4.5*fr,200)/total);
 f[n++]=lg(peak/maxd(rms,EPS));f[n++]=lg(a4/maxd(var*var,EPS));f[n++]=lg(peak/maxd(absmean,EPS));f[n++]=lg(rms/maxd(absmean,EPS));f[n++]=a3/maxd(rms*rms*rms,EPS);
 for(int k=1;k<=18;k++){double e=energy(p,(k*.5-.15)*fr,(k*.5+.15)*fr);f[n++]=lg(e/total);f[n++]=lg(e);}
 for(int k=0;k<20;k++)f[n++]=lg(energy(p,10*k,10*(k+1))/total);
 double entropy=0,logsum=0,sum=0;for(int k=0;k<=200;k++){double q=p[k]/total;entropy-=q*log(q+EPS);logsum+=log(p[k]+EPS)/201;sum+=p[k]/201;}
 f[n++]=entropy/log(201.);f[n++]=lg(exp(logsum)/maxd(sum,EPS));
 double chunks[10],cm=0,cv=0;for(int c=0;c<10;c++){double v=0;for(int j=0;j<40;j++)v+=x[c*40+j]*x[c*40+j]/40;chunks[c]=sqrt(v);cm+=chunks[c]/10;}
 for(int c=0;c<10;c++) { cv+=(chunks[c]-cm)*(chunks[c]-cm)/10; }
 f[n++]=sqrt(cv)/maxd(cm,EPS);
 dft(x,0);double em=0,ev=0;
 for(int k=0;k<N;k++){double h=(k==0||k==200)?1:(k<200?2:0);windowed[k]=re[k]*h;analytic_im[k]=-im[k]*h;}
 dft(windowed,analytic_im);
 for(int j=0;j<N;j++){env[j]=hypot(re[j],im[j])/N;em+=env[j]/N;}
 for(int j=0;j<N;j++) { ev+=(env[j]-em)*(env[j]-em)/N; }
 f[n++]=sqrt(ev)/maxd(em,EPS);
 psd(env,ep);double etotal=maxd(energy(ep,0,201),EPS);for(int k=1;k<=6;k++)f[n++]=lg(energy(ep,(k*.5-.15)*fr,(k*.5+.15)*fr)/etotal);
 for(int k=0;k<=24;k++){ac[k]=0;for(int j=0;j<N-k;j++)ac[k]+=x[j]*x[j+k]/N;}
 for(int i=0;i<24;i++){for(int j=0;j<24;j++)matrix[i][j]=ac[i>j?i-j:j-i]+(i==j?var*1e-6:0);matrix[i][24]=ac[i+1];}
 for(int k=0;k<24;k++){
  int pivot=k;for(int i=k+1;i<24;i++)if(fabs(matrix[i][k])>fabs(matrix[pivot][k]))pivot=i;
  if(fabs(matrix[pivot][k])<1e-30)return 0;
  if(pivot!=k)for(int j=k;j<=24;j++){double t=matrix[k][j];matrix[k][j]=matrix[pivot][j];matrix[pivot][j]=t;}
  double v=matrix[k][k];for(int j=k;j<=24;j++)matrix[k][j]/=v;
  for(int i=0;i<24;i++)if(i!=k){double q=matrix[i][k];for(int j=k;j<=24;j++)matrix[i][j]-=q*matrix[k][j];}
 }
 double residual=ac[0];for(int i=0;i<24;i++){f[n++]=matrix[i][24];residual-=matrix[i][24]*ac[i+1];}
 f[n++]=lg(residual/maxd(var,EPS));
 if(n!=101)return 0;
 for(int i=0;i<101;i++)if(!isfinite(f[i]))return 0;
 return 1;
}
int ml_reference_predict(const double f[101],double scores[4]){
 for(int i=0;i<101;i++)if(!isfinite(f[i]))return -1;
 int best=0;for(int c=0;c<4;c++){
  /* Fold standardization and linear mean are folded algebraically offline. */
  scores[c]=ML_ENSEMBLE_BIAS[c];for(int i=0;i<101;i++)scores[c]+=f[i]*ML_ENSEMBLE_WEIGHT[c][i];
  if(!isfinite(scores[c])){for(int j=0;j<4;j++)scores[j]=NAN;return -1;}
  if(scores[c]>scores[best])best=c;
 }return best;
}
