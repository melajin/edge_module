"""Mechanical faults v3 trial-held-out experiment, with protected one-shot lock evaluation."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import zipfile
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from sklearn.neighbors import NearestCentroid
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC, SVC
from .a_sources import prepare_a, selected_records, ROLES, write_json
from .a_features import PROFILES, ROTATION_HZ, extract_a, family_names
from .a_download_dev import download_dev, OUTPUT
from .experiment import environment
from .sources import sha256_file

CLASSES=("normal","imbalance","misalignment","mechanical_looseness")
SEED=20261002
META={"member","trial","role","label","channel","numeric_sha256"}
DEPENDENCIES=("a_sources.py","a_features.py","a_experiment.py","a_download_dev.py","sources.py","experiment.py")

def code_hashes():
    parent=Path(__file__).parent
    return {n:sha256_file(parent/n) for n in DEPENDENCIES}

def contract(output:Path,profile):
    return {"source_manifest_sha256":sha256_file(output/'source_manifest.json'),
            "features_code_sha256":sha256_file(Path(__file__).parent/'a_features.py'),
            "source_reader_code_sha256":sha256_file(Path(__file__).parent/'a_sources.py'),
            "profile":profile,"nominal_rotation_hz":ROTATION_HZ,
            "selection_indices":[180,240],"roles":{str(k):list(v) for k,v in ROLES.items()}}

def numeric_integrity(records):
    seen={}
    for r in records:
        for digest in [r['numeric_sha256'],*r['channel_numeric_sha256']]:
            if digest in seen and seen[digest]!=r['role']:
                raise ValueError("Identical numeric A waveform across roles")
            seen[digest]=r['role']

def build_features(output,profile,allow_lock=False):
    source=json.loads((output/'source_manifest.json').read_text(encoding='utf-8'))
    rows=[]
    raw_records=[]
    access=[]
    for trial in source['trials']:
        if trial['role']=='lock' and not allow_lock:
            access.append({'trial':trial['trial'],'role':'lock','raw_opened':False})
            continue
        records=selected_records(output,trial,allow_lock=allow_lock)
        raw_records.extend(r for r,w in records)
        for record,wave in records:
            for channel in range(4):
                features=extract_a(wave[channel],profile)
                rows.append({'member':record['name'],'trial':record['trial'],'role':record['role'],
                             'label':record['label'],'channel':channel,
                             'numeric_sha256':record['channel_numeric_sha256'][channel],**features})
        access.append({'trial':trial['trial'],'role':trial['role'],'raw_opened':True,'n_records':len(records)})
        print(f"A features {profile} trial {trial['trial']:02d} {trial['role']}",flush=True)
    numeric_integrity(raw_records)
    frame=pd.DataFrame(rows)
    cache=output/f"{profile}_{'lock' if allow_lock else 'dev'}_features.csv"
    frame.to_csv(cache,index=False)
    write_json(cache.with_suffix('.manifest.json'),{**contract(output,profile),'cache_sha256':sha256_file(cache),
               'lock_raw_opened':allow_lock,'access':access,
               'selected_integrity_sha256':{f"trial_{t['trial']:02d}":sha256_file(output/'sources'/f"trial_{t['trial']:02d}"/'selected_integrity.json') for t in source['trials'] if t['role']!='lock' or allow_lock}})
    return frame

def load_dev_features(output,profile):
    path=output/f'{profile}_dev_features.csv'
    if not path.exists():
        return build_features(output,profile)
    record=json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    expected=contract(output,profile)
    if any(record.get(k)!=v for k,v in expected.items()) or record['cache_sha256']!=sha256_file(path):
        raise ValueError("A cache source/code/role/profile/content changed")
    if record['lock_raw_opened'] or any(x['role']=='lock' and x['raw_opened'] for x in record['access']):
        raise ValueError("Development cache includes lock")
    for trial,integrity_hash in record['selected_integrity_sha256'].items():
        if sha256_file(output/'sources'/trial/'selected_integrity.json')!=integrity_hash:
            raise ValueError("Selected raw integrity manifest changed")
    frame=pd.read_csv(path)
    if set(frame.role)!={'calibration','train','dev'}:
        raise ValueError("Invalid role in A development features")
    seen={}
    for row in frame.itertuples():
        if row.numeric_sha256 in seen and seen[row.numeric_sha256]!=row.role:
            raise ValueError("A cache numeric duplicate across roles")
        seen[row.numeric_sha256]=row.role
    return frame

def transform(frame,names,representation):
    raw=frame[names].to_numpy(dtype=float)
    if representation=='raw':
        return raw
    if representation!='normal_relative':
        raise ValueError(representation)
    baseline=frame[frame.role=='calibration']
    if len(baseline)!=60 or set(baseline.trial)!={1} or baseline.label.nunique()!=1 or baseline.label.iloc[0]!='normal':
        raise ValueError("Calibration must be all 60 predeclared normal trial 01 records on this channel")
    median=np.median(baseline[names].to_numpy(dtype=float),axis=0)
    return raw-median

def models(round_name):
    if round_name=='round01':
        return {'centroid':make_pipeline(StandardScaler(),NearestCentroid()),
                'linear_c1':make_pipeline(StandardScaler(),LinearSVC(C=1,class_weight='balanced',random_state=SEED,max_iter=20000)),
                'rbf_c10':make_pipeline(StandardScaler(),SVC(C=10,gamma='scale',class_weight='balanced')),
                'rf':RandomForestClassifier(n_estimators=180,min_samples_leaf=2,max_features='sqrt',class_weight='balanced',random_state=SEED,n_jobs=2),
                'extra':ExtraTreesClassifier(n_estimators=220,min_samples_leaf=1,max_features=.7,class_weight='balanced',random_state=SEED,n_jobs=2)}
    if round_name=='round02':
        return {'rbf_c100':make_pipeline(StandardScaler(),SVC(C=100,gamma='scale',class_weight='balanced')),
                'rbf_g001':make_pipeline(StandardScaler(),SVC(C=20,gamma=.01,class_weight='balanced')),
                'rbf_g01':make_pipeline(StandardScaler(),SVC(C=20,gamma=.1,class_weight='balanced')),
                'extra_full':ExtraTreesClassifier(n_estimators=280,min_samples_leaf=1,max_features=1.,class_weight='balanced',random_state=SEED,n_jobs=2)}
    raise ValueError(round_name)

def metrics(truth,predicted):
    truth,predicted=np.asarray(truth),np.asarray(predicted)
    precision,recall,f1,support=precision_recall_fscore_support(truth,predicted,labels=list(CLASSES),zero_division=0)
    normal=truth=='normal'
    return {'classes':{c:{'precision':float(precision[i]),'recall':float(recall[i]),'f1':float(f1[i]),'support':int(support[i])} for i,c in enumerate(CLASSES)},
            'confusion':confusion_matrix(truth,predicted,labels=list(CLASSES)).tolist(),'labels':list(CLASSES),
            'accuracy':float(np.mean(truth==predicted)),'macro_f1':float(f1.mean()),
            'minimum_precision_recall':float(min(precision.min(),recall.min())),
            'strict_90_all_classes':bool(np.all(precision>.90)&np.all(recall>.90)),
            'normal_false_positive_rate':float(np.mean(predicted[normal]!='normal')) if normal.any() else None,
            'coverage':1.,'n':len(truth)}

def cluster_intervals(frame,predicted,replicates=1000):
    work=frame.copy()
    work['predicted']=predicted
    pools={c:[g for _,g in subset.groupby('trial')] for c,subset in work.groupby('label')}
    rng=np.random.default_rng(SEED)
    samples={f'{c}.{m}':[] for c in CLASSES for m in ('precision','recall')}
    for _ in range(replicates):
        selected=pd.concat([groups[i] for groups in pools.values() for i in rng.integers(0,len(groups),size=len(groups))])
        score=metrics(selected.label,selected.predicted)
        for c in CLASSES:
            for m in ('precision','recall'):
                samples[f'{c}.{m}'].append(score['classes'][c][m])
    return {'method':'class-stratified whole-trial bootstrap','replicates':replicates,
            'n_trials_by_class':{k:len(v) for k,v in pools.items()},
            'intervals_95':{k:np.quantile(v,[.025,.975]).tolist() for k,v in samples.items()},
            'warning':'One dev trial/class gives degenerate intervals; two lock trials/class remain insufficient for reliable uncertainty about new machines.'}

def report(path,frame,predicted):
    path.mkdir(exist_ok=True)
    result=metrics(frame.label,predicted)
    write_json(path/'metrics.json',result)
    write_json(path/'cluster_uncertainty.json',cluster_intervals(frame,predicted))
    predictions=frame[[c for c in frame.columns if c in META]].copy()
    predictions['predicted']=predicted
    predictions['correct']=predictions.label==predictions.predicted
    predictions.to_csv(path/'predictions.csv',index=False)
    write_json(path/'by_trial.json',[{'trial':int(t),'label':g.label.iloc[0],'n':len(g),'recall':float(g.correct.mean()),'prediction_counts':dict(Counter(g.predicted))} for t,g in predictions.groupby('trial')])
    fig,ax=plt.subplots(figsize=(7.4,6.6))
    matrix=np.array(result['confusion'])
    ax.imshow(matrix,cmap='Blues')
    for i in range(4):
        for j in range(4):
            ax.text(j,i,str(matrix[i,j]),ha='center',va='center',color='white' if matrix[i,j]>.6*matrix.max() else 'black')
    ax.set(xticks=range(4),yticks=range(4),xticklabels=CLASSES,yticklabels=CLASSES,xlabel='Predicted',ylabel='True',title='Trial held out; all 60 selected records retained')
    plt.setp(ax.get_xticklabels(),rotation=30,ha='right')
    fig.tight_layout()
    fig.savefig(path/'confusion.png',dpi=170)
    plt.close(fig)
    return result

def develop(output,profile,round_name):
    if (output/'freeze.json').exists() or (output/'lock_access_started.json').exists():
        raise RuntimeError("This branch is frozen/lock consumed; no further tuning allowed")
    directory=output/profile/round_name
    directory.mkdir(parents=True,exist_ok=False)
    planned={'phase':'development','profile':profile,'round':round_name,'channels':[0,1,2,3],
             'families':['base5','time_order','spectral','ar24','extended'],'representations':['raw','normal_relative'],
             'models':{k:{n:repr(v) for n,v in m.get_params().items()} for k,m in models(round_name).items()},
             'source_contract':contract(output,profile),'code_sha256':code_hashes(),'seed':SEED,
             'timestamp_utc':datetime.now(timezone.utc).isoformat(),
             'selection_rule':'maximum minimum class precision/recall, then macroF1, then fewer features',
             'primary_input':'one fixed radial channel; four candidate channels declared before fit',
             'fit_roles':['train'],'selection_roles':['dev'],'lock_raw_opened':False}
    write_json(directory/'plan.json',planned)
    frame=load_dev_features(output,profile)
    all_names=[n for n in frame.columns if n not in META]
    candidates=[]
    specifications=[]
    for channel in range(4):
        channel_frame=frame[frame.channel==channel].copy()
        train=channel_frame.role=='train'
        dev=channel_frame.role=='dev'
        if (train.sum(),dev.sum())!=(420,240):
            raise ValueError("Expected 420 train and 240 dev records per channel")
        for family in planned['families']:
            names=family_names(all_names,family)
            for representation in planned['representations']:
                x=transform(channel_frame,names,representation)
                for model_name,estimator in models(round_name).items():
                    identifier=f'ch{channel}__{family}__{representation}__{model_name}'
                    estimator.fit(x[train],channel_frame.loc[train,'label'])
                    predicted=estimator.predict(x[dev])
                    result=metrics(channel_frame.loc[dev,'label'],predicted)
                    candidate={'id':identifier,'channel':channel,'family':family,'representation':representation,
                               'model':model_name,'n_features':len(names),**result}
                    candidates.append(candidate)
                    artifact={'estimator':estimator,'features':names,'representation':representation,
                              'channel':channel,'profile':profile,'id':identifier}
                    joblib.dump(artifact,directory/(identifier+'.joblib'),compress=3)
                    specifications.append({'id':identifier,'features':names,'parameters':{k:repr(v) for k,v in estimator.get_params().items()}})
                    print(f"A {profile} {identifier}: minP/R={result['minimum_precision_recall']:.4f} macroF1={result['macro_f1']:.4f}",flush=True)
    candidates.sort(key=lambda c:(-c['minimum_precision_recall'],-c['macro_f1'],c['n_features'],c['id']))
    write_json(directory/'candidates.json',candidates)
    write_json(directory/'model_specifications.json',specifications)
    pd.DataFrame([{k:v for k,v in c.items() if not isinstance(v,(dict,list))} for c in candidates]).to_csv(directory/'leaderboard.csv',index=False)
    best=candidates[0]
    artifact=joblib.load(directory/(best['id']+'.joblib'))
    chosen_frame=frame[frame.channel==artifact['channel']]
    x=transform(chosen_frame,artifact['features'],artifact['representation'])
    dev=chosen_frame.role=='dev'
    predicted=artifact['estimator'].predict(x[dev])
    report(directory,chosen_frame.loc[dev],predicted)
    importance=permutation_importance(artifact['estimator'],x[dev],chosen_frame.loc[dev,'label'],scoring='f1_macro',n_repeats=5,random_state=SEED,n_jobs=2)
    pd.DataFrame({'feature':artifact['features'],'dev_permutation_delta_macro_f1':importance.importances_mean,'std':importance.importances_std}).sort_values('dev_permutation_delta_macro_f1',ascending=False).to_csv(directory/'feature_importance.csv',index=False)
    write_json(directory/'run_manifest.json',{'plan_sha256':sha256_file(directory/'plan.json'),'code_sha256':code_hashes(),
               'source_contract':contract(output,profile),'feature_cache_sha256':sha256_file(output/f'{profile}_dev_features.csv'),
               'model_sha256':{p.name:sha256_file(p) for p in directory.glob('*.joblib')},
               'candidates_sha256':sha256_file(directory/'candidates.json'),'best':best,'environment':environment(),
               'record_counts_per_channel':{'calibration':60,'train':420,'dev':240},
               'calibration_role':'normal trial01 held apart; constant per-feature median per channel',
               'normal_relative_effect':'Translation-invariant StandardScaler/linear/tree/RBF models should be equivalent to raw under fixed global median; ablation is a check, not assumed improvement',
               'lock_raw_opened':False,'hardware_measurement':False})
    print(json.dumps(best,indent=2),flush=True)
    return best

def freeze(output,profile,round_name,candidate):
    target=output/'freeze.json'
    if target.exists():
        raise RuntimeError("Preserve existing A freeze")
    directory=output/profile/round_name
    run=json.loads((directory/'run_manifest.json').read_text(encoding='utf-8'))
    if run['code_sha256']!=code_hashes() or run['source_contract']!=contract(output,profile):
        raise ValueError("Development source/code/role/profile changed")
    if run['plan_sha256']!=sha256_file(directory/'plan.json') or run['candidates_sha256']!=sha256_file(directory/'candidates.json'):
        raise ValueError("Plan/candidate metrics changed")
    if run['feature_cache_sha256']!=sha256_file(output/f'{profile}_dev_features.csv'):
        raise ValueError("Development features changed")
    candidates=json.loads((directory/'candidates.json').read_text(encoding='utf-8'))
    selected=next(c for c in candidates if c['id']==candidate)
    if not selected['strict_90_all_classes']:
        raise ValueError("Not all development class precision/recall strictly exceed .90")
    model=directory/(candidate+'.joblib')
    if run['model_sha256'][model.name]!=sha256_file(model):
        raise ValueError("Model changed since development")
    write_json(target,{'profile':profile,'round':round_name,'candidate':candidate,'metrics':selected,
               'code_sha256':code_hashes(),'source_contract':contract(output,profile),'model_path':str(model),
               'model_sha256':sha256_file(model),'run_manifest_sha256':sha256_file(directory/'run_manifest.json'),
               'timestamp_utc':datetime.now(timezone.utc).isoformat(),'lock_raw_opened':False})
    return json.loads(target.read_text(encoding='utf-8'))

def evaluate_lock(output,authorization):
    freeze_path=output/'freeze.json'
    record=json.loads(freeze_path.read_text(encoding='utf-8'))
    permission=json.loads(authorization.read_text(encoding='utf-8'))
    if permission.get('phase')!='approve-lock' or permission.get('freeze_sha256')!=sha256_file(freeze_path):
        raise PermissionError("Main-agent freeze-hash authorization required")
    if record['code_sha256']!=code_hashes() or record['source_contract']!=contract(output,record['profile']):
        raise ValueError("Frozen source/code/role/profile changed")
    model=Path(record['model_path'])
    if record['model_sha256']!=sha256_file(model):
        raise ValueError("Frozen model changed")
    with (output/'lock_access_started.json').open('x',encoding='utf-8') as stream:
        json.dump({'freeze_sha256':sha256_file(freeze_path),'authorization_sha256':sha256_file(authorization),
                   'timestamp_utc':datetime.now(timezone.utc).isoformat(),'status':'lock consumed even if process fails'},stream,indent=2)
    artifact=joblib.load(model)
    frame=build_features(output,record['profile'],allow_lock=True)
    channel_frame=frame[frame.channel==artifact['channel']]
    x=transform(channel_frame,artifact['features'],artifact['representation'])
    lock=channel_frame.role=='lock'
    predicted=artifact['estimator'].predict(x[lock])
    directory=output/record['profile']/'lock'
    directory.mkdir(exist_ok=False)
    result=report(directory,channel_frame.loc[lock],predicted)
    write_json(directory/'evaluation_manifest.json',{'freeze':record,'authorization':permission,
               'metrics':result,'whole_zip_sha256_verified':False,'all_selected_lock_records_retained':True,
               'n_lock_trials':8,'n_lock_records_per_trial':60,'hardware_measurement':False,
               'scope':'Normal/imbalance/misalignment/mechanical looseness, same test bench, new dismantled/rebuilt trial',
               'new_machine_verified':False})
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','download-dev','features','develop','freeze','evaluate-lock'))
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--profile',choices=PROFILES,default='limited1000')
    parser.add_argument('--round',choices=('round01','round02'),default='round01')
    parser.add_argument('--candidate')
    parser.add_argument('--authorization',type=Path)
    args=parser.parse_args()
    if args.command=='prepare':
        prepare_a(args.output)
    elif args.command=='download-dev':
        download_dev(args.output)
    elif args.command=='features':
        load_dev_features(args.output,args.profile)
    elif args.command=='develop':
        develop(args.output,args.profile,args.round)
    elif args.command=='freeze':
        if not args.candidate:
            parser.error('--candidate required')
        print(json.dumps(freeze(args.output,args.profile,args.round,args.candidate),indent=2))
    else:
        if not args.authorization:
            parser.error('--authorization required')
        print(json.dumps(evaluate_lock(args.output,args.authorization),indent=2))

if __name__=='__main__':
    main()
