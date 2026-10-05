"""Develop group-held-out candidates and learning curves from the declared A trials.

Records the feature, split, model-fold, and source contracts under the selected output directory.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import NearestCentroid
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC,SVC
from .a_experiment import CLASSES,META,metrics,cluster_intervals,environment
from .a_features import family_names
from .a_sources import ROLES,write_json
from .sources import sha256_file

REPOSITORY_ROOT=Path(__file__).resolve().parents[2]
DEFAULT_SOURCE=REPOSITORY_ROOT.parent/'output/fault_type_90_mechanical_A_20261002'
if not DEFAULT_SOURCE.exists():
    DEFAULT_SOURCE=REPOSITORY_ROOT/'output/fault_type_90_mechanical_A_20261002'
SOURCE=Path(os.environ.get('EDGE_REPORT_ML_SOURCE', DEFAULT_SOURCE))
OUTPUT=Path(os.environ.get('EDGE_REPORT_ML_OUTPUT', REPOSITORY_ROOT/'artifacts/report_model/development'))
PROFILES=('limited400','limited800','limited1000')
FAMILIES=('base5','ar24','time_order','extended')
INPUTS=('single_ch2','array_4ch')
MODEL_NAMES=('linear_c1','rf','rbf_c10','centroid')
SEED=20261002

def folds():
    result={}
    for label in CLASSES:
        trials=sorted(t for t,(c,old_role) in ROLES.items() if c==label)
        if len(trials)!=5:
            raise ValueError('Exactly five trials/class are required')
        result.update({trial:i for i,trial in enumerate(trials)})
    return result

def models(name):
    if name=='linear_c1':
        return make_pipeline(StandardScaler(),LinearSVC(C=1,class_weight='balanced',random_state=SEED,max_iter=30000))
    if name=='rf':
        return RandomForestClassifier(n_estimators=180,min_samples_leaf=2,max_features='sqrt',class_weight='balanced',random_state=SEED,n_jobs=2)
    if name=='rbf_c10':
        return make_pipeline(StandardScaler(),SVC(C=10,gamma='scale',class_weight='balanced'))
    if name=='centroid':
        return make_pipeline(StandardScaler(),NearestCentroid())
    raise ValueError(name)

def exact_numeric_guard(frame):
    seen={}
    for r in frame.itertuples():
        if r.numeric_sha256 in seen and seen[r.numeric_sha256]!=r.trial:
            raise ValueError('Same raw numeric channel across distinct trials')
        seen[r.numeric_sha256]=r.trial

def merge_caches(first,second):
    keys=['trial','member','channel']
    if first.duplicated(keys).any() or second.duplicated(keys).any():
        raise ValueError('Duplicate record identity within a cache')
    if list(first.columns)!=list(second.columns):
        raise ValueError('Feature cache columns differ')
    a=first.set_index(keys)
    b=second.set_index(keys)
    shared=a.index.intersection(b.index)
    numeric=[n for n in first if n not in META]
    for key in shared:
        ra,rb=a.loc[key],b.loc[key]
        if any(ra[n]!=rb[n] for n in ('label','role','numeric_sha256')):
            raise ValueError('Shared cache identity has contradictory provenance')
        if not np.allclose(ra[numeric].to_numpy(dtype=float),rb[numeric].to_numpy(dtype=float),rtol=1e-12,atol=1e-14):
            raise ValueError('Shared raw feature computations differ')
    combined=pd.concat([first,second],ignore_index=True).drop_duplicates(keys).sort_values(keys).reset_index(drop=True)
    exact_numeric_guard(combined)
    return combined,{'shared_identical_rows':len(shared),'first_rows':len(first),'second_rows':len(second),'unique_rows':len(combined)}

def load_profile(source,profile):
    frames=[]
    hashes={}
    for old_phase in ('dev','lock'):
        path=source/f'{profile}_{old_phase}_features.csv'
        provenance=path.with_suffix('.manifest.json')
        manifest=json.loads(provenance.read_text(encoding='utf-8'))
        if manifest['profile']!=profile or manifest['cache_sha256']!=sha256_file(path):
            raise ValueError('Existing feature cache hash/profile mismatch')
        if manifest['source_manifest_sha256']!=sha256_file(source/'source_manifest.json'):
            raise ValueError('Publisher source manifest differs from feature provenance')
        for name,field in [('a_features.py','features_code_sha256'),('a_sources.py','source_reader_code_sha256')]:
            if manifest[field]!=sha256_file(Path(__file__).parent/name):
                raise ValueError('Original frozen feature/source implementation changed')
        for trial,expected in manifest['selected_integrity_sha256'].items():
            if expected!=sha256_file(source/'sources'/trial/'selected_integrity.json'):
                raise ValueError('Original numeric integrity evidence changed')
        frames.append(pd.read_csv(path))
        hashes[old_phase]={'cache_sha256':sha256_file(path),'manifest_sha256':sha256_file(provenance)}
    combined,merge=merge_caches(*frames)
    if len(combined)!=4800 or combined.trial.nunique()!=20:
        raise ValueError('Expected all 20 consumed trials x60 records x4 channels')
    for trial,group in combined.groupby('trial'):
        if len(group)!=240 or group.member.nunique()!=60 or set(group.channel)!={0,1,2,3} or set(group.label)!={ROLES[trial][0]}:
            raise ValueError('Trial/channel/class denominator changed')
    combined['development_fold']=combined.trial.map(folds())
    return combined,{'original_cache_hashes':hashes,'merge':merge,'profile':profile}

def input_matrix(frame,family,input_kind):
    names=family_names([n for n in frame if n not in META and n!='development_fold'],family)
    keys=['trial','member','label','development_fold']
    if input_kind=='single_ch2':
        chosen=frame[frame.channel==2].sort_values(['trial','member']).copy()
        rows=chosen[keys+['numeric_sha256']].reset_index(drop=True)
        rows['channel']='2'
        return rows,chosen[names].to_numpy(dtype=float),names
    if input_kind!='array_4ch':
        raise ValueError(input_kind)
    result=None
    predictors=[]
    for channel in range(4):
        chosen=frame[frame.channel==channel][keys+['numeric_sha256']+names].copy()
        mapping={n:f'ch{channel}.{n}' for n in names}
        chosen=chosen.rename(columns={**mapping,'numeric_sha256':f'ch{channel}_numeric_sha256'})
        predictors.extend(mapping.values())
        result=chosen if result is None else result.merge(chosen,on=keys,how='outer',validate='one_to_one')
    result=result.sort_values(['trial','member']).reset_index(drop=True)
    if result.isna().any().any() or len(result)!=1200:
        raise ValueError('Array records have missing channels')
    rows=result[keys+[f'ch{i}_numeric_sha256' for i in range(4)]].copy()
    rows['channel']='0+1+2+3'
    return rows,result[predictors].to_numpy(dtype=float),predictors

def membership(rows,holdout_fold,train_trials_per_class=4):
    test=rows.development_fold.to_numpy()==holdout_fold
    chosen=[]
    for label in CLASSES:
        available=sorted(rows.loc[(rows.label==label)&~test,'trial'].unique())
        chosen.extend(available[:train_trials_per_class])
    train=rows.trial.isin(chosen).to_numpy()
    if set(rows.loc[train,'trial'])&set(rows.loc[test,'trial']):
        raise ValueError('A trial overlaps training and evaluation')
    return train,test

def aggregate_prediction_records(parts):
    result=pd.concat(parts,ignore_index=True).sort_values(['trial','member'])
    if len(result)!=1200 or result.duplicated(['trial','member']).any() or any(n!=60 for n in result.groupby('trial').size()):
        raise ValueError('OOF coverage must be exactly 1200 unique records')
    return result

def prediction_part(rows,test,predicted,estimator,x,fold):
    part=rows.loc[test].copy()
    part['outer_fold']=fold
    part['predicted']=predicted
    part['correct']=part.label.to_numpy()==predicted
    if hasattr(estimator,'decision_function'):
        scores=estimator.decision_function(x[test])
        prefix='decision_'
    else:
        scores=estimator.predict_proba(x[test])
        prefix='model_probability_'
    for label in CLASSES:
        part[prefix+label]=scores[:,list(estimator.classes_).index(label)]
    ordered=np.sort(scores,axis=1)
    part['top_second_margin']=ordered[:,-1]-ordered[:,-2]
    return part

def save_result(directory,predictions):
    directory.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(directory/'oof_predictions.csv',index=False)
    predictions[~predictions.correct].to_csv(directory/'all_errors.csv',index=False)
    result=metrics(predictions.label,predictions.predicted)
    write_json(directory/'metrics.json',result)
    write_json(directory/'whole_trial_uncertainty.json',cluster_intervals(predictions,predictions.predicted))
    write_json(directory/'by_trial.json',[{'trial':int(t),'label':g.label.iloc[0],'fold':int(g.outer_fold.iloc[0]),'n':len(g),'recall':float(g.correct.mean())} for t,g in predictions.groupby('trial')])
    write_json(directory/'by_fold.json',[{'fold':int(f),**metrics(g.label,g.predicted)} for f,g in predictions.groupby('outer_fold')])
    return result

def group_cv(rows,x,model_name,directory,train_trials_per_class=4):
    parts=[]
    fit_records=[]
    for fold in range(5):
        train,test=membership(rows,fold,train_trials_per_class)
        estimator=models(model_name)
        estimator.fit(x[train],rows.loc[train,'label'])
        predicted=estimator.predict(x[test])
        parts.append(prediction_part(rows,test,predicted,estimator,x,fold))
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/f'fold{fold}.joblib'
        joblib.dump(estimator,path,compress=3)
        fit_records.append({'fold':fold,'train_trials':sorted(int(v) for v in rows.loc[train,'trial'].unique()),
                            'test_trials':sorted(int(v) for v in rows.loc[test,'trial'].unique()),
                            'n_train':int(train.sum()),'n_test':int(test.sum()),'model_sha256':sha256_file(path)})
    predictions=aggregate_prediction_records(parts)
    result=save_result(directory,predictions)
    write_json(directory/'fit_provenance.json',fit_records)
    return result

def initialize(source,output):
    output.mkdir(parents=True,exist_ok=False)
    previous_batch=json.loads((source/'batch_freeze.json').read_text(encoding='utf-8'))
    from .a_final_batch import all_code_hashes
    if previous_batch['code_sha256']!=all_code_hashes():
        raise ValueError('Original frozen core/batch changed')
    data_contract={n:sha256_file(source/n) for n in ('source_manifest.json','freeze.json','batch_freeze.json','final_batch_results.json')}
    plan={'phase':'development only, all20 original trials consumed; no fresh independent test',
          'timestamp_utc':datetime.now(timezone.utc).isoformat(),'source_directory':str(source),
          'original_contract_sha256':data_contract,'profiles':list(PROFILES),'families':list(FAMILIES),
          'inputs':list(INPUTS),'models':{n:{k:repr(v) for k,v in models(n).get_params().items()} for n in MODEL_NAMES},
          'trial_to_outer_fold':{str(k):v for k,v in folds().items()},
          'split_unit':'whole rebuilt trial, every time and channel share fold',
          'candidate_count':len(PROFILES)*len(FAMILIES)*len(INPUTS)*len(MODEL_NAMES),
          'selection_rule':'greatest minimum four-class precision/recall, then macroF1, then fewer features, then ID',
          'representations':['original raw physical features; signed AR unchanged'],
          'array_requires':'four spatially separate radial accelerometers, not a single ADXL345',
          'learning_curve':{'profile':'limited1000','input':'single_ch2','features':'ar24','model':'linear_c1',
                            'trials_per_class':[1,2,3,4],'train_subset_rule':'first sorted available trial IDs excluding each fixed holdout'},
          'nested_if_promising':{'trigger':'best candidate all-class precision/recall>.90',
                                 'inner_folds':4,'selection':'only bounded four models, within each predeclared input/profile/family set',
                                 'outer_folds':5,'still_development':True},
          'code_sha256':sha256_file(Path(__file__)),'environment':environment(),'raw_reopened':False,
          'fresh_test_certification':False,'hardware_measurement':False}
    write_json(output/'plan.json',plan)
    return plan

def run(source=SOURCE,output=OUTPUT):
    plan=initialize(source,output)
    candidates=[]
    cache_provenance=[]
    learning=[]
    for profile in PROFILES:
        frame,provenance=load_profile(source,profile)
        cache_provenance.append(provenance)
        frame.to_csv(output/f'{profile}_merged_development.csv',index=False)
        for input_kind in INPUTS:
            for family in FAMILIES:
                rows,x,names=input_matrix(frame,family,input_kind)
                for model_name in MODEL_NAMES:
                    identifier=f'{profile}__{input_kind}__{family}__{model_name}'
                    directory=output/'candidates'/identifier
                    result=group_cv(rows,x,model_name,directory)
                    write_json(directory/'feature_contract.json',{'profile':profile,'input':input_kind,'family':family,'features':names,'predictor_metadata':False,'n_features':len(names)})
                    candidate={'id':identifier,'profile':profile,'input':input_kind,'family':family,'model':model_name,'n_features':len(names),**result}
                    candidates.append(candidate)
                    write_json(output/'running_history.json',candidates)
                    print(f"groupCV {identifier}: minP/R={result['minimum_precision_recall']:.4f} macroF1={result['macro_f1']:.4f}",flush=True)
        if profile=='limited1000':
            rows,x,names=input_matrix(frame,'ar24','single_ch2')
            for count in (1,2,3,4):
                directory=output/'learning_curve'/f'{count}_trials_per_class'
                result=group_cv(rows,x,'linear_c1',directory,train_trials_per_class=count)
                learning.append({'train_trials_per_class':count,**result})
                print('learningcurve',count,result['minimum_precision_recall'],flush=True)
    candidates.sort(key=lambda c:(-c['minimum_precision_recall'],-c['macro_f1'],c['n_features'],c['id']))
    write_json(output/'candidates.json',candidates)
    write_json(output/'feature_cache_provenance.json',cache_provenance)
    write_json(output/'learning_curve.json',learning)
    pd.DataFrame([{k:v for k,v in c.items() if not isinstance(v,(dict,list))} for c in candidates]).to_csv(output/'leaderboard.csv',index=False)
    write_json(output/'run_manifest.json',{'plan_sha256':sha256_file(output/'plan.json'),'code_sha256':sha256_file(Path(__file__)),
               'candidate_sha256':sha256_file(output/'candidates.json'),'original_contract_sha256':plan['original_contract_sha256'],
               'merged_development_hashes':{p.name:sha256_file(p) for p in output.glob('*_merged_development.csv')},
               'best':candidates[0],'fresh_test_certification':False,'raw_reopened':False,
               'independent_trial_count':20,'records':1200,'records_per_class':300,'folds':5})
    print(json.dumps(candidates[0],indent=2),flush=True)
    return candidates

def nested(source,output,profile,input_kind,family):
    directory=output/'nested'/f'{profile}__{input_kind}__{family}'
    directory.mkdir(parents=True,exist_ok=False)
    original=json.loads((output/'plan.json').read_text(encoding='utf-8'))
    if (profile not in original['profiles'] or input_kind not in original['inputs'] or family not in original['families']):
        raise ValueError('Nested set outside the development plan')
    frame,provenance=load_profile(source,profile)
    rows,x,names=input_matrix(frame,family,input_kind)
    write_json(directory/'nested_plan.json',{'profile':profile,'input':input_kind,'family':family,'features':names,'models':list(MODEL_NAMES),
               'code_sha256':sha256_file(Path(__file__)),'still_development':True,'selection_unit':'inner whole trial',
               'known_development_material':True,'source_provenance':provenance})
    parts=[]
    selection=[]
    for outer in range(5):
        outer_train,outer_test=membership(rows,outer)
        inner_fold_ids=[f for f in range(5) if f!=outer]
        scores=[]
        for model_name in MODEL_NAMES:
            inner_truth=[]
            inner_prediction=[]
            for inner in inner_fold_ids:
                train=outer_train&(rows.development_fold.to_numpy()!=inner)
                test=outer_train&(rows.development_fold.to_numpy()==inner)
                estimator=models(model_name)
                estimator.fit(x[train],rows.loc[train,'label'])
                inner_truth.extend(rows.loc[test,'label'])
                inner_prediction.extend(estimator.predict(x[test]))
            result=metrics(inner_truth,inner_prediction)
            scores.append({'model':model_name,**result})
        scores.sort(key=lambda c:(-c['minimum_precision_recall'],-c['macro_f1'],c['model']))
        chosen=scores[0]['model']
        estimator=models(chosen)
        estimator.fit(x[outer_train],rows.loc[outer_train,'label'])
        predicted=estimator.predict(x[outer_test])
        parts.append(prediction_part(rows,outer_test,predicted,estimator,x,outer))
        joblib.dump(estimator,directory/f'outer{outer}.joblib',compress=3)
        selection.append({'outer_fold':outer,'chosen':chosen,'inner_oof':scores,
                          'outer_train_trials':sorted(int(t) for t in rows.loc[outer_train,'trial'].unique()),
                          'outer_test_trials':sorted(int(t) for t in rows.loc[outer_test,'trial'].unique())})
        print('nested',outer,chosen,scores[0]['minimum_precision_recall'],flush=True)
    result=save_result(directory,aggregate_prediction_records(parts))
    write_json(directory/'selection.json',selection)
    print(json.dumps(result,indent=2),flush=True)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('develop','nested'))
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--profile',choices=PROFILES,default='limited1000')
    parser.add_argument('--input',choices=INPUTS,default='single_ch2')
    parser.add_argument('--family',choices=FAMILIES,default='ar24')
    args=parser.parse_args()
    if args.command=='develop':
        run(args.source,args.output)
    else:
        nested(args.source,args.output,args.profile,args.input,args.family)

if __name__=='__main__':
    main()
