#include "em_cwru.h"
#include "generated/em_cwru_model.h"
#include <math.h>
#include <string.h>
#define PI 3.14159265358979323846264338327950288
int em_cwru_features(const double *x, size_t n, double fs, double rpm,
                     em_cwru_scratch *s, double f[6]) {
    size_t i,j,len; double mean=0,m2=0,m4=0, rms2=0, residual_mean=0, v[6],total=1e-12;
    double peak=-1, rotation, bandwidth; double *re,*im;
    if (!x || !s || !f || n!=EM_CWRU_N || fs!=12000.0 || !isfinite(rpm) || rpm<=0 || rpm/60>=fs/2) return -1;
    for(i=0;i<n;i++) { if(!isfinite(x[i])) return -1; mean+=x[i]/n; }
    for(i=1;i<n && x[i]==x[0];i++) {}
    if(i==n) return -1; /* scipy kurtosis of any constant is undefined. */
    re=s->real; im=s->imag;
    for(i=0;i<n;i++) { double a=x[i]-mean; re[i]=a;im[i]=0;rms2+=a*a/n;residual_mean+=a/n; }
    /* scipy.stats.kurtosis re-centers its input when computing moments. */
    for(i=0;i<n;i++) {double a=re[i]-residual_mean,b=a*a;m2+=b/n;m4+=b*b/n;}
    if(!isfinite(m4) || m2<=0 || !isfinite(m2)) return -1;
    v[0]=sqrt(rms2);v[1]=m4/(m2*m2)-3.0;
    for(i=1,j=0;i<n;i++) {size_t bit=n>>1; for(;j&bit;bit>>=1) j^=bit; j^=bit;
        if(i<j) { double t=re[i];re[i]=re[j];re[j]=t; } }
    for(len=2;len<=n;len<<=1) {
        double wr0=cos(-2*PI/len),wi0=sin(-2*PI/len);
        for(i=0;i<n;i+=len) {double wr=1,wi=0;
            for(j=0;j<len/2;j++) {size_t a=i+j,b=a+len/2;
                double tr=wr*re[b]-wi*im[b],ti=wr*im[b]+wi*re[b],next=wr*wr0-wi*wi0;
                re[b]=re[a]-tr;im[b]=im[a]-ti;re[a]+=tr;im[a]+=ti;wi=wr*wi0+wi*wr0;wr=next;
            }
        }
    }
    rotation=rpm/60;bandwidth=fmax(5.0,2*fs/n);
    for(i=0;i<n/2;i++) {double hz=(double)i*fs/n;re[i]=hypot(re[i],im[i])*2/n;
        if(hz>=rpm/60*0.8 && hz<=rpm/60*1.2 && re[i]>peak) {peak=re[i];rotation=hz;}
        if(hz>1) total+=re[i];
    }
    v[2]=v[3]=v[4]=v[5]=0;
    for(i=0;i<n/2;i++) {double hz=(double)i*fs/n;
        for(j=1;j<=3;j++) if(hz>=rotation*j-bandwidth && hz<=rotation*j+bandwidth) v[j+1]+=re[i];
        if(hz>=rotation*10 && hz<=fs/2*0.8) v[5]+=re[i];
    }
    for(i=2;i<6;i++) v[i]/=total;
    for(i=0;i<6;i++) if(!isfinite(v[i])) return -1;
    memcpy(f,v,sizeof(v));return 0;
}
int em_cwru_predict(const double f[6], int *index, double decision[4]) {
    double scaled[6],scores[4];int i,j,best=0;
    if(!f || !index) return -1;
    for(i=0;i<6;i++) { if(!isfinite(f[i])) return -1;scaled[i]=(f[i]-em_cwru_mean[i])/em_cwru_scale[i]; }
    for(i=0;i<4;i++) {scores[i]=em_cwru_intercept[i];for(j=0;j<6;j++) scores[i]+=em_cwru_coef[i][j]*scaled[j];
        if(!isfinite(scores[i])) return -1;
        if(scores[i]>scores[best]) best=i;
    }
    *index=best;if(decision) memcpy(decision,scores,sizeof(scores));return 0;
}
void em_cwru_vote_reset(em_cwru_vote *s) {if(s) memset(s,0,sizeof(*s));}
int em_cwru_vote_update(em_cwru_vote *s,int index,int *evaluable,int *confirmed) {
    unsigned i,sum=0;
    if(!s || !evaluable || !confirmed || index<0 || index>=4 || s->count>5 || s->next>=5) return -1;
    for(i=0;i<5;i++) if(s->history[i]>1) return -1;
    s->history[s->next]=(unsigned char)(index!=2);s->next=(s->next+1)%5;if(s->count<5) s->count++;
    for(i=0;i<5;i++) sum+=s->history[i];
    *evaluable=(s->count==5);*confirmed=(*evaluable && sum>=4);return 0;
}
