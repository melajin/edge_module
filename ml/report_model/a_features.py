"""Physical vibration features; each model uses one fixed radial channel unless declared array."""
from __future__ import annotations
import numpy as np
from scipy import signal, linalg

PROFILES={"native25000":25000,"limited1000":1000,"limited800":800,"limited400":400}
ROTATION_HZ=1238/60
EPS=1e-20

def restrict(wave: np.ndarray, profile: str):
    fs=PROFILES[profile]
    if fs==25000:
        return np.asarray(wave,dtype=float),float(fs)
    divisor=np.gcd(25000,fs)
    return signal.resample_poly(wave,fs//divisor,25000//divisor),float(fs)

def extract_a(wave: np.ndarray, profile: str):
    x,fs=restrict(wave,profile)
    x=x-x.mean()
    var=float(np.mean(x*x))
    rms=np.sqrt(var)
    f,p=signal.periodogram(x,fs=fs,window="hann",scaling="spectrum")
    total=max(float(p.sum()),EPS)
    def log(v):
        return float(np.log(max(float(v),EPS)))
    def energy(lo,hi):
        return float(p[(f>=lo)&(f<hi)].sum())
    order=f/ROTATION_HZ
    values={"log_rms":log(rms)}
    # Adapted physical five-feature baseline. 1-second available waveform, nominal rotation,
    # Hann PSD, inclusive physical 5Hz+2FFT-bin harmonic bands. Not bit-identical V2 firmware.
    bandwidth=5+2*fs/len(x)
    for k in (1,2,3):
        values[f"log_h{k}"]=log(energy(k*ROTATION_HZ-bandwidth,k*ROTATION_HZ+bandwidth)/total)
    values["log_hf_fraction"]=log(energy(4.5*ROTATION_HZ,min(255,fs/2))/total)
    absolute=np.abs(x)
    meanabs=max(float(absolute.mean()),EPS)
    values.update({"log_crest":log(absolute.max()/max(rms,EPS)),
                   "log_kurtosis":log(np.mean(x**4)/max(var**2,EPS)),
                   "log_impulse":log(absolute.max()/meanabs),"log_shape":log(rms/meanabs),
                   "skewness":float(np.mean(x**3))/max(rms**3,EPS)})
    maxorder=min(12.,.95*(fs/2)/ROTATION_HZ)
    for k in np.arange(.5,maxorder+.001,.5):
        e=float(p[(order>=k-.15)&(order<k+.15)].sum())
        values[f"log_order_{k:.1f}"]=log(e/total)
        values[f"log_order_abs_{k:.1f}"]=log(e)
    # Fixed-Hz resonance shape: represent all accessible spectrum without class-specific bands.
    boundaries=np.unique(np.r_[np.arange(0,min(200,fs/2),10),
                                   np.arange(200,min(1000,fs/2),50),
                                   np.arange(1000,fs/2,500),fs/2])
    for lo,hi in zip(boundaries[:-1],boundaries[1:]):
        values[f"log_hz_band_{int(lo)}_{int(hi)}"]=log(energy(lo,hi)/total)
    distribution=p/total
    values["spectral_entropy"]=float(-np.sum(distribution*np.log(distribution+EPS))/np.log(len(p)))
    values["log_spectral_flatness"]=log(np.exp(np.mean(np.log(p+EPS)))/max(p.mean(),EPS))
    chunks=np.array([np.sqrt(np.mean(a*a)) for a in np.array_split(x,10)])
    envelope=np.abs(signal.hilbert(x))
    values["rms_cv"]=float(chunks.std()/max(chunks.mean(),EPS))
    values["envelope_cv"]=float(envelope.std()/max(envelope.mean(),EPS))
    ef,ep=signal.periodogram(envelope-envelope.mean(),fs=fs,window="hann",scaling="spectrum")
    eorder=ef/ROTATION_HZ
    etotal=max(float(ep.sum()),EPS)
    for k in (.5,1,1.5,2,2.5,3):
        values[f"log_envelope_order_{k:.1f}"]=log(ep[(eorder>=k-.15)&(eorder<k+.15)].sum()/etotal)
    ar_order=24
    ac=signal.correlate(x,x,mode="full",method="fft")[len(x)-1:len(x)+ar_order]/len(x)
    coefficients=linalg.solve_toeplitz(ac[:-1]+np.r_[var*1e-6,np.zeros(ar_order-1)],ac[1:])
    for i,c in enumerate(coefficients,1):
        values[f"ar24_{i:02d}"]=float(c)
    values["log_ar24_residual_fraction"]=log((ac[0]-np.dot(coefficients,ac[1:]))/max(var,EPS))
    if not np.isfinite(list(values.values())).all():
        raise ValueError("A features contain nonfinite values")
    return values

def family_names(names,family):
    base=["log_rms","log_h1","log_h2","log_h3","log_hf_fraction"]
    if family=="base5":
        return base
    if family=="time_order":
        return [n for n in names if not (n.startswith("log_hz_band_") or n.startswith("ar24_") or n.startswith("log_ar24"))]
    if family=="spectral":
        return [n for n in names if not (n.startswith("ar24_") or n.startswith("log_ar24"))]
    if family=="ar24":
        return [n for n in names if n=="log_rms" or n.startswith("ar24_") or n.startswith("log_ar24")]
    if family=="extended":
        return list(names)
    raise ValueError(family)
