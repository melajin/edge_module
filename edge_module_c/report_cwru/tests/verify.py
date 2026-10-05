"""Same firmware adapter/C core on PC; frozen MAT replay, NOT board execution."""
from pathlib import Path
import ctypes as ct
import csv
import hashlib
import json
import argparse
import subprocess
import sys
import tempfile
import unittest
import numpy as np
from scipy.io import loadmat

ROOT=Path(__file__).resolve().parents[1]
reference_manifest=json.loads((ROOT/'python_reference/hashes.json').read_text(encoding='utf-8'))
for name,expected_hash in reference_manifest.items():
    actual_hash=hashlib.sha256((ROOT/'python_reference'/name).read_bytes()).hexdigest()
    assert actual_hash==expected_hash,(name,actual_hash,expected_hash)
sys.path.insert(0,str(ROOT/'python_reference'))
from aihub_training_rewrite import cwru
import joblib
OUT=ROOT/'verification'
commands=[]
def run(command):
    commands.append(command)
    subprocess.run(command,check=True)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
class Input(ct.Structure):
    _fields_=[('session',ct.c_uint64),('sequence',ct.c_uint64),('sample_start',ct.c_uint64),
              ('fs',ct.c_double),('rpm',ct.c_double),('dropped',ct.c_uint32)]
class Fault(ct.Structure):
    _fields_=[('status',ct.c_int),('positive',ct.c_uint8),('valid',ct.c_uint8),
              ('instant_valid',ct.c_bool),('flag',ct.c_bool)]
class Output(ct.Structure):
    _fields_=[('features',ct.c_double*6),('scores',ct.c_double*4),('index',ct.c_int),('bearing',Fault)]
run(['gcc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-I'+str(ROOT/'include'),
     '-o',str(OUT/'bearing_adapter.dll'),str(ROOT/'tests/host_bridge.c'),
     *[str(ROOT/'src'/name) for name in ['bearing_adapter.c','em_cwru.c','em_v3.c']],'-lm'])
lib=ct.CDLL(str(OUT/'bearing_adapter.dll'))
D=ct.POINTER(ct.c_double)
lib.test_submit.argtypes=[ct.POINTER(Input),D,ct.c_size_t,ct.c_uint32]
lib.test_output.restype=ct.POINTER(Output)
lib.test_expire.argtypes=[ct.c_uint32]
lib.test_low_updates.argtypes=[ct.c_uint]
lib.test_alias.argtypes=[ct.POINTER(Input),D,ct.c_uint32]
lib.test_alias_once.argtypes=[ct.POINTER(Input),D]
lib.test_alias_once.restype=ct.POINTER(Output)
def output(): return Output.from_buffer_copy(bytes(lib.test_output().contents))
def submit(raw,index=0,now=None,**kw):
    raw=np.ascontiguousarray(raw,dtype=np.float64)
    data=Input(kw.get('session',1),kw.get('sequence',index),kw.get('start',index*2048),
               kw.get('fs',12000),kw.get('rpm',1797),kw.get('dropped',0))
    return lib.test_submit(ct.byref(data),raw.ctypes.data_as(D),kw.get('count',len(raw)),
                           now if now is not None else index*171)
signal=np.random.default_rng(17).normal(size=4096)
class Contract(unittest.TestCase):
    def setUp(self): lib.test_reset()
    def test_first_four_then_single_policy(self):
        flags=[]
        for i in range(8):
            self.assertEqual(submit(signal,i),0)
            result=output(); flags.append(result.index!=2)
            self.assertEqual(result.bearing.valid,min(i+1,5))
            self.assertEqual(result.bearing.positive,sum(flags[-5:]))
            self.assertEqual(result.bearing.status,0 if i<4 else (2 if sum(flags[-5:])>=4 else 1))
    def test_invalid_inputs_fail_closed(self):
        for kw in [{'count':4095},{'fs':400},{'fs':float('nan')},{'rpm':0},
                   {'rpm':float('nan')},{'rpm':360000},{'dropped':1}]:
            lib.test_reset(); self.assertEqual(submit(signal),0)
            self.assertLess(submit(signal,1,**kw),0)
            self.assertEqual(output().bearing.valid,0)
        for bad in [float('nan'),float('inf')]:
            lib.test_reset(); x=signal.copy(); x[200]=bad
            self.assertLess(submit(x),0); self.assertEqual(output().bearing.status,0)
        self.assertLess(submit(np.zeros(4096)),0)
    def test_duplicate_out_of_order_no_second_vote(self):
        for seq,start in [(0,0),(0,2048),(1,0)]:
            lib.test_reset();submit(signal)
            self.assertEqual(submit(signal,sequence=seq,start=start),-2)
            self.assertEqual(output().bearing.valid,0)
        submit(signal)
        self.assertEqual(submit(signal,sequence=0,start=0,rpm=1772),-2)
        self.assertEqual(output().bearing.valid,0)
    def test_uart_scratch_imag_alias(self):
        submit(signal);expected=output()
        lib.test_reset()
        data=Input(1,0,0,12000,1797,0)
        self.assertEqual(lib.test_alias(ct.byref(data),signal.ctypes.data_as(D),0),0)
        actual=output()
        np.testing.assert_array_equal(actual.features,expected.features)
        np.testing.assert_array_equal(actual.scores,expected.scores)
        self.assertEqual(actual.index,expected.index)
    def test_session_gap_rpm_change_and_drop_warmup(self):
        for kw in [{'session':2},{'sequence':9},{'start':9999},{'rpm':1772}]:
            lib.test_reset()
            for i in range(5):submit(signal,i)
            self.assertEqual(submit(signal,5,**kw),1)
            self.assertEqual(output().bearing.valid,1)
            self.assertEqual(output().bearing.status,0)
        self.assertLess(submit(signal,6,dropped=1),0)
        self.assertEqual(submit(signal,7),0)
        self.assertEqual(output().bearing.valid,1)
    def test_freshness_boundaries_and_clock_wrap(self):
        submit(signal,now=0xfffffff0)
        lib.test_expire(0x7c0) # 2000 ms exactly
        self.assertEqual(output().bearing.valid,1)
        lib.test_expire(0x7c1)
        self.assertEqual(output().bearing.valid,0)
        submit(signal,now=1)
        self.assertEqual(submit(signal,1,now=2002),1)
        self.assertEqual(output().bearing.valid,1)
    def test_low_band_does_not_vote_or_erase_bearing(self):
        for i in range(5):submit(signal,i)
        before=bytes(output())
        self.assertEqual(lib.test_low_updates(100),0)
        self.assertEqual(bytes(output()),before)

def locked():
    pinned={}
    def check(path,expected,label):
        actual=sha(path); assert actual==expected,(label,actual,expected)
        pinned[str(path)]={'sha256':actual,'label':label}
    check(cwru.DEFAULT_MANIFEST,cwru.ACTIVE_MANIFEST_SHA256,'active manifest')
    check(cwru.DEFAULT_RESULT,cwru.RESULT_SHA256,'binary reference result')
    ref=json.loads(cwru.DEFAULT_RESULT.read_text(encoding='utf-8'))
    subject=next(r for r in json.loads(cwru.DEFAULT_MANIFEST.read_text(encoding='utf-8'))['subjects'] if r['subject']=='cwru')
    manifest=cwru.DEFAULT_MANIFEST.parent/subject['manifest']
    check(manifest,cwru.CWRU_MANIFEST_SHA256,'CWRU manifest')
    training=ROOT/'artifacts/cwru'
    check(training/'model.joblib',cwru.MODEL_SHA256,'frozen model')
    check(training/'selection.json',cwru.SELECTION_SHA256,'selection')
    payload=joblib.load(training/'model.joblib')
    records=[r for r in json.loads(manifest.read_text(encoding='utf-8'))['records'] if r['role']=='locked_test']
    assert [r['id'] for r in records]==ref['inputs']['locked_test_file_order']
    classes=['ball','inner_race','normal','outer_race']
    matrix=np.zeros((4,4),dtype=int); truth=[];predicted=[];rows=[];groups=[]
    feature_error=score_error=0.0
    for session,r in enumerate(records,1):
        source=Path(r['source']); check(source,r['source_sha256'],'raw MAT')
        raw=np.asarray(loadmat(source,variable_names=[r['mat_channel']])[r['mat_channel']]).ravel()
        assert hashlib.sha256(raw.astype('<f8').tobytes()).hexdigest()==r['content_sha256']
        lib.test_reset(); flags=[];scored=[];correct=0; count=0
        for index,start in enumerate(range(0,len(raw)-4095,2048)):
            x=raw[start:start+4096]
            assert submit(x,index,session=session,rpm=r['source_metadata'][2])==0
            actual=output();expected=np.asarray(cwru._record_feature(x,r['sample_rate_hz'],r['source_metadata'][2]))
            # Exercise the exact UART scratch.imag alias for every locked raw
            # window independently of the main temporal replay state.
            alias_data=Input(session,index,start,12000,r['source_metadata'][2],0)
            alias_raw=np.ascontiguousarray(x,dtype=np.float64)
            alias=lib.test_alias_once(ct.byref(alias_data),alias_raw.ctypes.data_as(D)).contents
            np.testing.assert_array_equal(alias.features,actual.features)
            np.testing.assert_array_equal(alias.scores,actual.scores)
            assert alias.index==actual.index
            af=np.array(actual.features);scores=np.array(actual.scores)
            es=payload['model'].decision_function([expected])[0]
            ep=int(payload['model'].predict([expected])[0])
            np.testing.assert_allclose(af,expected,rtol=2e-10,atol=1e-12)
            np.testing.assert_allclose(scores,es,rtol=2e-10,atol=1e-10)
            assert actual.index==ep
            feature_error=max(feature_error,float(np.max(np.abs(af-expected))))
            score_error=max(score_error,float(np.max(np.abs(scores-es))))
            flags.append(ep!=2); matrix[classes.index(r['label']),ep]+=1
            count+=1;correct+=int(classes[ep]==r['label'])
            if index<4: assert actual.bearing.status==0
            else:
                confirmed=actual.bearing.status==2
                assert confirmed==(sum(flags[-5:])>=4)
                truth.append(r['label']!='normal');predicted.append(confirmed);scored.append(confirmed)
            rows.append({'file_id':r['id'],'session':session,'window':index,'sample_start':start,
                         'true_class':r['label'],'predicted_class':classes[ep],
                         'instant_flag':int(ep!=2),'status':actual.bearing.status,
                         'votes_positive':actual.bearing.positive,'votes_valid':actual.bearing.valid,
                         **{name:af[i] for i,name in enumerate(cwru.FEATURE_NAMES)},
                         **{'score_'+name:scores[i] for i,name in enumerate(classes)}})
        assert scored==cwru.confirm_four_of_five(flags)[0]
        groups.append({'id':r['id'],'label':r['label'],'source':str(source),'windows':count,
                       'warmup_windows':4,'scored_windows':len(scored),'subtype_correct':correct,
                       'confirmed':sum(scored),'source_metadata':r['source_metadata']})
    metrics=cwru._metrics(truth,predicted)
    assert metrics==ref['cwru']['overall']
    frozen=json.loads((training/'locked_test_result.json').read_text(encoding='utf-8'))
    assert matrix.tolist()==frozen['metrics']['confusion_matrix']
    assert float(np.trace(matrix)/matrix.sum())==frozen['metrics']['accuracy']
    # The independent Python predictions already match every window; retain
    # subtype reference document as additional pinned evidence.
    pinned[str(training/'locked_test_result.json')]={'sha256':sha(training/'locked_test_result.json'),'label':'subtype result inspected'}
    result={'scope':'host same firmware C adapter raw MAT replay; no board execution',
            'files':len(records),'source_windows':len(rows),'scored_windows':len(truth),
            'max_feature_abs_error':feature_error,'max_score_abs_error':score_error,
            'uart_alias_windows_verified':len(rows),
            'class_order':classes,'subtype_confusion_matrix':matrix.tolist(),
            'subtype_accuracy':float(np.trace(matrix)/matrix.sum()),'binary_4_of_5':metrics,
            'groups':groups,'pinned_inputs':pinned,
            'firmware_profile':{'fs_hz':12000,'window':4096,'stride':2048,'input_unit':'model convention g',
                'hf_numerator_upper_hz':4800,'total_denominator_upper_hz':'just below 6000',
                'freshness_ms':2000,'uart_replay_baud':460800}}
    with (OUT/'locked_windows.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (OUT/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    (OUT/'host_commands.json').write_text(json.dumps(commands,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['groups','pinned_inputs']},indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-only',action='store_true',help='run host software contract tests without raw MAT replay')
    parser.add_argument('--output-dir',type=Path,help='directory for temporary build and optional replay outputs')
    args=parser.parse_args()
    temporary=None
    if args.output_dir:
        OUT=args.output_dir.resolve();OUT.mkdir(parents=True,exist_ok=True)
    else:
        temporary=tempfile.TemporaryDirectory(prefix='edge-report-cwru-')
        OUT=Path(temporary.name)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contract))
    if not result.wasSuccessful():sys.exit(1)
    if not args.basic_only:locked()
    if sys.platform=='win32':
        handle=lib._handle;lib._handle=0
        free_library=ct.windll.kernel32.FreeLibrary
        free_library.argtypes=[ct.c_void_p];free_library.restype=ct.c_int
        if not free_library(ct.c_void_p(handle)):raise OSError('Could not unload the CWRU host-test DLL')
    lib=None
    if temporary is not None:temporary.cleanup()
