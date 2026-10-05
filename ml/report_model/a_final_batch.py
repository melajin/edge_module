"""Narrow frozen four-profile endpoint batch; no fitting or lock-driven selection."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from . import a_experiment as experiment
from .a_audit import export as export_primary
from .a_sources import write_json
from .sources import sha256_file
from .a_download_dev import OUTPUT

PRIMARY=('limited1000','round01','ch2__ar24__raw__linear_c1')
SUPPLEMENTAL_PROFILES=('limited400','limited800','native25000')

def all_code_hashes():
    values=experiment.code_hashes()
    parent=Path(__file__).parent
    for name in ('a_final_batch.py','a_audit.py'):
        values[name]=sha256_file(parent/name)
    return values

def verify_development(output,profile,round_name,candidate):
    directory=output/profile/round_name
    run_path=directory/'run_manifest.json'
    run=json.loads(run_path.read_text(encoding='utf-8'))
    if run['code_sha256']!=experiment.code_hashes() or run['source_contract']!=experiment.contract(output,profile):
        raise ValueError('Endpoint source/code/split/profile changed since development')
    for file_name,field in [('plan.json','plan_sha256'),('candidates.json','candidates_sha256')]:
        if sha256_file(directory/file_name)!=run[field]:
            raise ValueError('Endpoint plan or candidate metrics changed')
    cache=output/f'{profile}_dev_features.csv'
    if run['feature_cache_sha256']!=sha256_file(cache):
        raise ValueError('Endpoint development cache changed')
    candidates=json.loads((directory/'candidates.json').read_text(encoding='utf-8'))
    selected=next(c for c in candidates if c['id']==candidate)
    model=directory/(candidate+'.joblib')
    if run['model_sha256'][model.name]!=sha256_file(model):
        raise ValueError('Endpoint model changed')
    # Full cache contract validation reads only the predeclared development data table.
    experiment.load_dev_features(output,profile)
    return {'profile':profile,'round':round_name,'candidate':candidate,'development_metrics':selected,
            'model_path':str(model),'model_sha256':sha256_file(model),
            'run_manifest_path':str(run_path),'run_manifest_sha256':sha256_file(run_path),
            'development_cache_sha256':sha256_file(cache),'source_contract':experiment.contract(output,profile)}

def freeze_batch(output=OUTPUT):
    target=output/'batch_freeze.json'
    if target.exists() or (output/'batch_access_started.json').exists():
        raise RuntimeError('Preserve original batch freeze and one-shot consumption record')
    primary=verify_development(output,*PRIMARY)
    if not primary['development_metrics']['strict_90_all_classes']:
        raise ValueError('Primary development endpoint does not meet the strict threshold')
    supplemental=[]
    for profile in SUPPLEMENTAL_PROFILES:
        candidates=json.loads((output/profile/'round01'/'candidates.json').read_text(encoding='utf-8'))
        candidate=candidates[0]['id']
        supplemental.append(verify_development(output,profile,'round01',candidate))
    # Only after every endpoint is verified, create the unchanged primary freeze.
    experiment.freeze(output,*PRIMARY)
    value={'primary':primary,'supplemental':supplemental,'code_sha256':all_code_hashes(),
           'primary_freeze_sha256':sha256_file(output/'freeze.json'),
           'source_manifest_sha256':sha256_file(output/'source_manifest.json'),
           'timestamp_utc':datetime.now(timezone.utc).isoformat(),
           'endpoints_fixed_before_lock':True,'primary_may_change_after_lock':False,
           'lock_record_contract':{'trials':[9,13,14,16,17,18,19,20],'records_per_trial':60,
                                   'total':480,'records_per_class':120,'trials_per_class':2,
                                   'sorted_selection_indices':[180,240]},
           'selection_reason':'Primary fixed to 1k raw AR26 linear. Other profiles use only their predeclared development winner for bandwidth comparison.',
           'source_integrity':'Exact selected ranges/member CRC+SHA; whole publisher ZIP SHA not verified',
           'hardware_measurement':False,'locked_raw_opened':False}
    write_json(target,value)
    print('Frozen batch SHA256:',sha256_file(target),flush=True)
    print('Primary freeze SHA256:',sha256_file(output/'freeze.json'),flush=True)
    return value

def validate_authorization(output,authorization):
    permission=json.loads(authorization.read_text(encoding='utf-8'))
    if permission.get('phase')!='approve-lock':
        raise PermissionError('Main lock phase authorization required')
    if permission.get('freeze_sha256')!=sha256_file(output/'freeze.json'):
        raise PermissionError('Primary freeze hash not authorized')
    if permission.get('batch_freeze_sha256')!=sha256_file(output/'batch_freeze.json'):
        raise PermissionError('Full primary+supplemental batch hash not authorized')
    bundle=json.loads((output/'batch_freeze.json').read_text(encoding='utf-8'))
    if bundle['code_sha256']!=all_code_hashes():
        raise ValueError('Frozen batch code changed')
    if bundle['primary_freeze_sha256']!=sha256_file(output/'freeze.json') or bundle['source_manifest_sha256']!=sha256_file(output/'source_manifest.json'):
        raise ValueError('Frozen primary or publisher source manifest changed')
    endpoints=[bundle['primary'],*bundle['supplemental']]
    if tuple(bundle['primary'][k] for k in ('profile','round','candidate'))!=PRIMARY:
        raise ValueError('Frozen primary was substituted')
    if tuple(p['profile'] for p in bundle['supplemental'])!=SUPPLEMENTAL_PROFILES:
        raise ValueError('Supplemental endpoint set/order changed')
    for endpoint in endpoints:
        verified=verify_development(output,endpoint['profile'],endpoint['round'],endpoint['candidate'])
        if verified!=endpoint:
            raise ValueError('Frozen endpoint metadata or hashes changed')
    return permission,bundle

def export_endpoint_scores(path,artifact,frame,x,predicted):
    estimator=artifact['estimator']
    prediction=frame[['trial','member','label','channel','numeric_sha256']].copy()
    prediction['profile']=artifact['profile']
    prediction['predicted']=predicted
    prediction['correct']=prediction.label.to_numpy()==predicted
    model=estimator.steps[-1][1] if isinstance(estimator,Pipeline) else estimator
    details={'features':artifact['features'],'class_order':list(estimator.classes_),
             'profile':artifact['profile'],'channel':artifact['channel'],'representation':artifact['representation']}
    if isinstance(estimator,Pipeline):
        scaler=estimator.steps[0][1]
        details['training_scaler']={'mean':scaler.mean_.tolist(),'scale':scaler.scale_.tolist(),'var':scaler.var_.tolist(),'fit_count':int(scaler.n_samples_seen_)}
    if hasattr(model,'coef_'):
        details['coef']=model.coef_.tolist()
        details['intercept']=model.intercept_.tolist()
    if hasattr(model,'centroids_'):
        details['centroids']=model.centroids_.tolist()
    if not hasattr(estimator,'decision_function'):
        raise ValueError('Frozen endpoint must expose four class decision scores')
    scores=estimator.decision_function(x)
    if scores.shape!=(len(frame),4):
        raise ValueError('Frozen endpoint score dimensions changed')
    for label in experiment.CLASSES:
        prediction['decision_'+label]=scores[:,list(estimator.classes_).index(label)]
    sorted_scores=np.sort(scores,axis=1)
    prediction['top_second_margin']=sorted_scores[:,-1]-sorted_scores[:,-2]
    prediction['actual_class_score']=[scores[i,list(estimator.classes_).index(c)] for i,c in enumerate(frame.label)]
    details['decision_argmax_equals_predict']=bool(np.array_equal(estimator.classes_[np.argmax(scores,axis=1)],predicted))
    prediction.to_csv(path/'all_decisions.csv',index=False)
    prediction[~prediction.correct].to_csv(path/'all_errors.csv',index=False)
    write_json(path/'score_summary.json',[{'class':c,'n':len(g),'correct':int(g.correct.sum()),'error':int((~g.correct).sum()),
                                         'margin_mean':float(g.top_second_margin.mean()),'margin_min':float(g.top_second_margin.min()),
                                         'actual_class_score_mean':float(g.actual_class_score.mean())} for c,g in prediction.groupby('label')])
    write_json(path/'model_parameters.json',details)

def check_lock_counts(frame):
    if len(frame)!=480 or frame.trial.nunique()!=8 or set(frame.trial)!={9,13,14,16,17,18,19,20}:
        raise ValueError('Frozen lock record/trial contract changed')
    if any(n!=60 for n in frame.groupby('trial').size()) or any(n!=120 for n in frame.groupby('label').size()):
        raise ValueError('No lock trial/class may be dropped or reweighted')

def evaluate_batch(output,authorization):
    permission,bundle=validate_authorization(output,authorization)
    if (output/'lock_access_started.json').exists():
        raise RuntimeError('Primary lock already consumed')
    # Exclusive global consumption marker exists before ANY locked raw read/download.
    with (output/'batch_access_started.json').open('x',encoding='utf-8') as stream:
        json.dump({'timestamp_utc':datetime.now(timezone.utc).isoformat(),
                   'authorization_sha256':sha256_file(authorization),'batch_freeze_sha256':sha256_file(output/'batch_freeze.json'),
                   'status':'All four endpoints consumed even if a later endpoint fails'},stream,indent=2)
    # Unchanged primary evaluates once with its own before-data exclusive marker.
    primary_result=experiment.evaluate_lock(output,authorization)
    export_primary(output,*PRIMARY,phase='lock')
    endpoints=[{'role':'primary','profile':PRIMARY[0],'candidate':PRIMARY[2],'metrics':primary_result}]
    for endpoint in bundle['supplemental']:
        artifact=joblib.load(endpoint['model_path'])
        frame=experiment.build_features(output,endpoint['profile'],allow_lock=True)
        chosen=frame[frame.channel==artifact['channel']]
        transformed=experiment.transform(chosen,artifact['features'],artifact['representation'])
        lock=chosen.role=='lock'
        lock_frame=chosen.loc[lock]
        check_lock_counts(lock_frame)
        x=transformed[lock]
        predicted=artifact['estimator'].predict(x)
        path=output/endpoint['profile']/'lock'
        path.mkdir(exist_ok=False)
        result=experiment.report(path,lock_frame,predicted)
        export_endpoint_scores(path,artifact,lock_frame,x,predicted)
        write_json(path/'evaluation_manifest.json',{'endpoint':endpoint,'authorization_sha256':sha256_file(authorization),
                   'batch_freeze_sha256':sha256_file(output/'batch_freeze.json'),'all_selected_records_retained':True,
                   'lock_consumed':True,'hardware_measurement':False,'selection_after_lock':False})
        endpoints.append({'role':'supplemental','profile':endpoint['profile'],'candidate':endpoint['candidate'],'metrics':result})
    primary_frame=pd.read_csv(output/PRIMARY[0]/'lock'/'predictions.csv')
    check_lock_counts(primary_frame)
    value={'primary':endpoints[0],'supplemental':endpoints[1:],'primary_selected_after_lock':False,
           'batch_freeze_sha256':sha256_file(output/'batch_freeze.json'),'authorization_sha256':sha256_file(authorization),
           'source':'Mechanical faults v3 only','independent_trial_support_per_class':2,'records_per_class':120,
           'scope':'Same bench/fixed approx1238RPM/central60seconds/trial-held-out; not a new machine or sensor test',
           'hardware_measurement':False,'unsupported_faults':['bearing','belt looseness']}
    write_json(output/'final_batch_results.json',value)
    print(json.dumps(value,indent=2),flush=True)
    return value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('freeze','evaluate'))
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--authorization',type=Path)
    args=parser.parse_args()
    if args.command=='freeze':
        freeze_batch(args.output)
    else:
        if not args.authorization:
            parser.error('--authorization required')
        evaluate_batch(args.output,args.authorization)

if __name__=='__main__':
    main()
