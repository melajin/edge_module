"""Export explanations and decision scores from immutable experiment artifacts; never fit."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from .a_experiment import CLASSES, code_hashes, load_dev_features, transform
from .a_features import PROFILES, ROTATION_HZ
from .a_sources import write_json
from .sources import sha256_file

def export(output: Path, profile: str, round_name: str, candidate: str, phase='dev'):
    directory=output/profile/round_name
    run=json.loads((directory/'run_manifest.json').read_text(encoding='utf-8'))
    model_path=directory/(candidate+'.joblib')
    if run['model_sha256'][model_path.name]!=sha256_file(model_path):
        raise ValueError('Model no longer matches development manifest')
    artifact=joblib.load(model_path)
    if phase=='dev':
        frame=load_dev_features(output,profile)
    elif phase=='lock':
        freeze=json.loads((output/'freeze.json').read_text(encoding='utf-8'))
        if (freeze['profile'],freeze['round'],freeze['candidate'])!=(profile,round_name,candidate):
            raise ValueError('Requested lock explanation is not the frozen model')
        if not (output/profile/'lock'/'metrics.json').exists():
            raise PermissionError('Lock may be explained only after authorized one-shot evaluation')
        cache=output/f'{profile}_lock_features.csv'
        cache_record=json.loads(cache.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        if cache_record['cache_sha256']!=sha256_file(cache):
            raise ValueError('Lock feature cache changed')
        frame=pd.read_csv(cache)
    else:
        raise ValueError(phase)
    chosen=frame[frame.channel==artifact['channel']]
    x=transform(chosen,artifact['features'],artifact['representation'])
    mask=chosen.role==phase
    selected=chosen[mask].copy()
    estimator=artifact['estimator']
    predicted=estimator.predict(x[mask])
    if isinstance(estimator,Pipeline):
        scaler=estimator.steps[0][1]
        classifier=estimator.steps[-1][1]
        standardized=scaler.transform(x[mask])
        scaling={'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist(),'var':scaler.var_.tolist(),
                 'fit_roles':['train'],'n_samples_seen':int(scaler.n_samples_seen_)}
    else:
        classifier=estimator
        standardized=x[mask]
        scaling=None
    target=output/profile/('lock' if phase=='lock' else round_name)/'audit'
    target.mkdir(parents=True,exist_ok=True)
    description={'candidate':candidate,'profile':profile,'channel_zero_based':artifact['channel'],
                 'channel_position':['pulley-side vertical','pulley-side horizontal','disk-side vertical','disk-side horizontal'][artifact['channel']],
                 'model_sha256':sha256_file(model_path),'features':artifact['features'],'scaler':scaling,
                 'class_order':list(classifier.classes_),'representation':artifact['representation'],
                 'nominal_rotation_hz':ROTATION_HZ,'input_sample_rate_hz':25000,'output_sample_rate_hz':PROFILES[profile],
                 'resampling':{'function':'scipy.signal.resample_poly','window':['kaiser',5.0],
                               'padtype':'constant','up':int(PROFILES[profile]//np.gcd(25000,PROFILES[profile])),
                               'down':int(25000//np.gcd(25000,PROFILES[profile])),
                               'hardware_transfer_function':False},
                 'feature_definitions':{'log_rms':'log(sqrt(mean((x-mean(x))^2)))',
                      'ar24_j':'Yule-Walker biased autocorrelation Toeplitz solve, lags 1..24, ridge R0*1e-6 on diagonal',
                      'log_ar24_residual_fraction':'log(max((R0 - sum(a_j*Rj))/R0,1e-20))',
                      'linear_class_score':'intercept_c + sum_j(coef_cj*(feature_j-train_mean_j)/train_scale_j)',
                      'prediction':'class with greatest classifier decision score, if available; otherwise estimator.predict',
                      'feature_contribution':'linear coefficient_cj times standardized feature_j; class bias is separate',
                      'meaning':'Statistical short-lag vibration dynamics, not proof of a mechanical fault cause'},
                 'export_code_sha256':sha256_file(Path(__file__)),
                 'hardware_measurement':False}
    if hasattr(classifier,'coef_'):
        description['coef']=classifier.coef_.tolist()
        description['intercept']=classifier.intercept_.tolist()
        weights=pd.DataFrame(classifier.coef_,columns=artifact['features'],index=classifier.classes_)
        weights.index.name='class'
        weights.to_csv(target/'linear_weights.csv')
        pd.DataFrame({'feature':artifact['features'],'mean':scaler.mean_,'scale':scaler.scale_,'var':scaler.var_}).to_csv(target/'training_scaler.csv',index=False)
    if hasattr(classifier,'centroids_'):
        description['standardized_centroids']=classifier.centroids_.tolist()
        description['priors']=classifier.class_prior_.tolist() if hasattr(classifier,'class_prior_') else None
    scores=estimator.decision_function(x[mask]) if hasattr(estimator,'decision_function') else None
    predictions=selected[['trial','member','label','channel','numeric_sha256']].copy()
    predictions['profile']=profile
    predictions['predicted']=predicted
    predictions['correct']=selected.label.to_numpy()==predicted
    if scores is not None:
        if scores.shape!=(len(selected),4):
            raise ValueError('Expected four class decision scores')
        score_classes=estimator.classes_
        for c in CLASSES:
            predictions['decision_'+c]=scores[:,list(score_classes).index(c)]
        sorted_score=np.sort(scores,axis=1)
        predictions['top_second_margin']=sorted_score[:,-1]-sorted_score[:,-2]
        predictions['actual_class_score']=[scores[i,list(score_classes).index(c)] for i,c in enumerate(selected.label)]
        if not np.array_equal(np.array(score_classes)[np.argmax(scores,axis=1)],predicted):
            description['decision_argmax_equals_predict']=False
        else:
            description['decision_argmax_equals_predict']=True
        if hasattr(classifier,'coef_'):
            predicted_indices=[list(classifier.classes_).index(c) for c in predicted]
            contribution=np.array([classifier.coef_[ci]*standardized[i] for i,ci in enumerate(predicted_indices)])
            pd.DataFrame(contribution,columns=artifact['features']).assign(trial=selected.trial.to_numpy(),member=selected.member.to_numpy(),predicted=predicted).to_csv(target/'predicted_class_contributions.csv',index=False)
            reconstructed=np.array([contribution[i].sum()+classifier.intercept_[ci] for i,ci in enumerate(predicted_indices)])
            if not np.allclose(reconstructed,np.max(scores,axis=1),atol=1e-10):
                raise ValueError('Linear contributions do not reconstruct classifier scores')
    predictions.to_csv(target/'all_decisions.csv',index=False)
    predictions[~predictions.correct].to_csv(target/'all_errors.csv',index=False)
    summaries=[]
    for label,group in predictions.groupby('label'):
        record={'class':label,'n':len(group),'correct':int(group.correct.sum()),'error':int((~group.correct).sum())}
        if scores is not None:
            record['margin_mean']=float(group.top_second_margin.mean())
            record['margin_min']=float(group.top_second_margin.min())
            record['actual_class_score_mean']=float(group.actual_class_score.mean())
        summaries.append(record)
    write_json(target/'score_summary.json',summaries)
    write_json(target/'model_explanation.json',description)
    candidates=json.loads((directory/'candidates.json').read_text(encoding='utf-8'))
    # Same channel, model, representation and split; only the feature family differs.
    model_name=next(c['model'] for c in candidates if c['id']==candidate)
    matched=[c for c in candidates if c['channel']==artifact['channel'] and c['model']==model_name]
    records=[]
    for c in matched:
        r={k:c[k] for k in ('id','channel','family','representation','model','n_features','minimum_precision_recall','macro_f1','accuracy')}
        for label in CLASSES:
            for m in ('precision','recall'):
                r[f'{label}.{m}']=c['classes'][label][m]
        records.append(r)
    pd.DataFrame(records).to_csv(target/'same_model_feature_ablation.csv',index=False)
    print('Saved immutable artifact explanation:',target,flush=True)
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('output/fault_type_90_mechanical_A_20261002'))
    parser.add_argument('--profile',default='limited1000')
    parser.add_argument('--round',default='round01')
    parser.add_argument('--candidate',required=True)
    parser.add_argument('--phase',choices=('dev','lock'),default='dev')
    args=parser.parse_args()
    export(args.output,args.profile,args.round,args.candidate,args.phase)
