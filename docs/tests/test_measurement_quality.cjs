/* Node/DOM stub checks of the actual inline browser script; not a visual test. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.listeners = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set innerHTML(value) { throw new Error(`unsafe HTML assignment: ${value}`); }
}
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); },
  createElement(tag) { return new Element(tag); },
};
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const context = vm.createContext({document, console});
vm.runInContext(script, context);
const parse = text => context.parseRecords(text);
const legacy = () => ({reason:'phase_history_warmup',fs_hz:null,fg_hz:null,rpm:null,
  amp_1x_g:null,ratio_1x:null,imbalance:'unavailable',imbalance_votes:0});
const row = (seq = 0, session = '0123456789abcdef') => ({...legacy(), samples:512,
  acquisition:{v:1,session,seq,start_us:1000000+seq*2000000,end_us:2277500+seq*2000000,
    gap_us:seq===0?null:722500,valid:true,reason:'ok'}});
const profile = () => ({event:'profile',v:1,command:'profile',ok:true,reason:'ok',firmware:'postreport_v2',
  profile:{selected:{context_id:'bench-1',ppr:2},current:null,candidate:null,previous:null,
    baseline_ready:false,learning:false,learn_count:0,paused:false,
    storage:{hold:false,reason:'ok',generation:0},legacy:{valid:false,reason:'absent'}}});
const baseline = (id=1) => ({id,context_id:'bench-1',ppr:2,amplitude_g:[.001,.2,4],learn_count:336,
  origin:'learned',session:'0123456789abcdef',seq:7});
const ready=profile(); Object.assign(ready.profile,{current:baseline(),candidate:baseline(2),previous:baseline(3),baseline_ready:true});
assert.equal(context.validateRecord(ready).length,0);
const fresh=profile(); Object.assign(fresh.profile.selected,{context_id:null,ppr:0});
assert.equal(context.validateRecord(fresh).length,0);
fresh.profile.selected.ppr=2; assert.equal(context.validateRecord(fresh).length,0);
const clone = v => JSON.parse(JSON.stringify(v));
for (const [parts,values] of [
  [['v'],[true,2]], [['event'],['unknown']], [['command'],['unknown',[]]], [['ok'],[1]],
  [['reason'],['<img>','x'.repeat(65),'']], [['firmware'],['v3']],
  [['profile','selected','ppr'],[true,-1,17,1.5]],
  [['profile','current','id'],[true,0,2**32]],
  [['profile','current','context_id'],[null,'x'.repeat(13),'<script>']],
  [['profile','current','ppr'],[0,17,true]],
  [['profile','current','seq'],[true,-1,2**53]],
  [['profile','current','session'],['0123456789ABCDEF',null]],
  [['profile','current','amplitude_g'],[[true,.2,1],[0,.2,1],[.1,.2,4.01],[.1,Infinity,1],[]]],
  [['profile','current','learn_count'],[true,0,335]],
  [['profile','current','origin'],['unknown',[]]],
  [['profile','storage','generation'],[true,-1,2**32]],
  [['profile','storage','hold'],[1,true]],
  [['profile','learn_count'],[true,-1,337,1]], [['profile','paused'],[1]],
  [['profile','learning'],[1]], [['profile','legacy','valid'],[1]],
]) for (const value of values) {
  const bad=clone(ready); let obj=bad;
  for (const k of parts.slice(0,-1)) obj=obj[k];
  obj[parts.at(-1)]=value;
  assert.ok(context.validateRecord(bad).length, parts.join('.')+': '+String(value));
}
for (const parts of [[],['profile'],['profile','selected'],['profile','current'],['profile','storage'],['profile','legacy']]) {
  let obj=ready; for (const k of parts) obj=obj[k];
  for (const key of Object.keys(obj)) {
    const bad=clone(ready); let at=bad; for (const k of parts) at=at[k];
    delete at[key]; assert.ok(context.validateRecord(bad).length,'missing '+key);
  }
  const bad=clone(ready); let at=bad; for (const k of parts) at=at[k];
  at.extra=1; assert.ok(context.validateRecord(bad).length,'unknown field');
}
for (const update of [p=>p.candidate.id=1,p=>p.selected.ppr=3,p=>p.current=null]) {
  const bad=clone(ready); update(bad.profile); assert.ok(context.validateRecord(bad).length);
}
const imported=clone(ready); Object.assign(imported.profile.current,{origin:'legacy_import',learn_count:0});
assert.equal(context.validateRecord(imported).length,0);
const learning=clone(ready); Object.assign(learning.profile,{learning:true,learn_count:7});
assert.equal(context.validateRecord(learning).length,0);
for (const [condition,id] of [[null,null],['bench-1',null],['bench-1',1]])
  assert.equal(context.validateRecord({...legacy(),context_id:condition,baseline_id:id}).length,0);
for (const extra of [{context_id:'bench-1'},{baseline_id:1},{context_id:null,baseline_id:1},
  {context_id:'<img>',baseline_id:null},{context_id:'bench-1',baseline_id:true}])
  assert.ok(context.validateRecord({...legacy(),...extra}).length);
const mixed=parse([legacy(),row(),ready,row(1),ready,row(3),ready,row(0,'fedcba9876543210')].map(JSON.stringify).join('\n'));
assert.equal(mixed.errors.length,0);
assert.deepEqual(Array.from(mixed.accepted.filter(r=>r.value.event!=='profile'),r=>r.transport.status),['unknown','first','contiguous','gap','session_changed']);
assert.ok(mixed.accepted.filter(r=>r.value.event==='profile').every(r=>r.transport===null));
const forgedEvent=clone(ready); forgedEvent.collector_report_transport={status:'forged'};
assert.equal(parse(JSON.stringify(forgedEvent)).accepted.length,0);
const malicious=clone(ready); malicious.profile.current.context_id='<img src=x onerror=alert(1)>';
assert.equal(parse(JSON.stringify(malicious)).accepted.length,0);
const lines = [row(0),row(1),row(3),row(3),row(2),row(4),row(0,'fedcba9876543210'),legacy(),row(2)];
const runtimeRow = (seq = 0) => ({...row(seq), runtime:{v:1,pre_emit_us:1283000,
  previous_emit:seq===0?null:{seq:seq-1,call_us:740},
  heap:{free_bytes:240000,min_free_bytes:220000,largest_free_bytes:120000}}});
assert.equal(context.validateRecord(runtimeRow()).length,0);
assert.equal(context.validateRecord(runtimeRow(1)).length,0);
for (const [key,value] of [['v',true],['v',2],['pre_emit_us',true],['pre_emit_us',-1],
  ['pre_emit_us',2**53],['pre_emit_us',1.5],['previous_emit',null],['previous_emit',true],['heap',[]]]) {
  const bad=runtimeRow(1); bad.runtime[key]=value;
  assert.ok(context.validateRecord(bad).length,`runtime.${key}`);
}
for (const [nested,keys] of [['previous_emit',['seq','call_us']],['heap',['free_bytes','min_free_bytes','largest_free_bytes']]]) {
  for (const key of keys) {
    for (const value of [true,-1,2**53,1.5,null]) {
      const bad=runtimeRow(1); bad.runtime[nested][key]=value;
      assert.ok(context.validateRecord(bad).length,`${nested}.${key}`);
    }
    const bad=runtimeRow(1); delete bad.runtime[nested][key];
    assert.ok(context.validateRecord(bad).length);
  }
}
for (const key of ['v','pre_emit_us','previous_emit','heap']) {
  const bad=runtimeRow(); delete bad.runtime[key]; assert.ok(context.validateRecord(bad).length);
}
for (const key of ['min_free_bytes','largest_free_bytes']) {
  const bad=runtimeRow(); bad.runtime.heap[key]=240001; assert.ok(context.validateRecord(bad).length);
}
const wrongPrevious=runtimeRow(1); wrongPrevious.runtime.previous_emit.seq=1;
assert.ok(context.validateRecord(wrongPrevious).length);
const missingAcquisition=runtimeRow(); delete missingAcquisition.acquisition;
assert.ok(context.validateRecord(missingAcquisition).length);
const nonnullFirst=runtimeRow(); nonnullFirst.runtime.previous_emit={seq:0,call_us:0};
assert.ok(context.validateRecord(nonnullFirst).length);
const result = parse(lines.map(JSON.stringify).join('\n')+'\n{broken\n'+JSON.stringify(row(3)));
assert.equal(result.errors.length,1);
assert.deepEqual(Array.from(result.accepted, r=>r.transport.status),
  ['first','contiguous','gap','duplicate','out_of_order','contiguous','session_changed','unknown','first','first']);
assert.equal(result.accepted[2].transport.missing,1);
for (const [key,value] of [['v',true],['seq',true],['seq',2**53],['session','short'],
  ['valid',1],['start_us',-1],['end_us',10],['gap_us',Infinity],['reason','partial_window']]) {
  const bad = row(); bad.acquisition[key] = value;
  assert.ok(context.validateRecord(bad).length, key);
}
for (const count of [true,-1,0,1,511,513]) {
  const bad = row(); bad.samples = count;
  assert.ok(context.validateRecord(bad).length);
}
const no = row(1); no.samples=0;
Object.assign(no.acquisition,{start_us:null,end_us:null,gap_us:null,valid:false,reason:'no_samples'});
assert.equal(context.validateRecord(no).length,0);
const noRuntime={...no,runtime:runtimeRow(1).runtime};
noRuntime.runtime.pre_emit_us=0;
assert.equal(context.validateRecord(noRuntime).length,0);
const partial=row(2); partial.samples=3;
Object.assign(partial.acquisition,{end_us:5005000,valid:false,reason:'partial_window'});
assert.equal(context.validateRecord(partial).length,0);
assert.equal(context.validateRecord({...row(), reason:'fg_missing'}).length,0);
const forged=row(); forged.collector_report_transport={status:'contiguous',missing:0};
assert.equal(parse(JSON.stringify(forged)).accepted[0].transport.status,'first');
const attack='<img src=x onerror=alert(1)>';
const xss={...legacy(),reason:attack};
const display=parse([legacy(),row(),no,partial,xss,runtimeRow(),runtimeRow(1),noRuntime].map(JSON.stringify).join('\n')).accepted;
context.renderRecords(display,attack+'.jsonl');
const flatten = element => element.textContent+' '+element.children.map(flatten).join(' ');
const rendered=flatten(elements.get('recordTableWrap'));
assert.ok(rendered.includes(attack));
assert.ok(rendered.includes('미확인'));
assert.ok(rendered.includes('관측 표본 없음'));
assert.ok(rendered.includes('partial_window'));
assert.ok(rendered.includes('상태 미관측'));
assert.ok(rendered.includes('수신 순번 (파일 내 계산)'));
assert.ok(rendered.includes('실행 관측 (µs / B)'));
assert.ok(rendered.includes('첫 결과 · 이전 호출 없음'));
assert.ok(rendered.includes('이전 순번 0: 740 µs'));
assert.ok(rendered.includes('allocator 영역별 최저 합 220000 B'));
assert.ok(rendered.includes('전송 완료/PC 수신 시간 미측정'));
assert.ok(rendered.includes('미확인 · 실행 관측 없음'));
context.renderRecords(mixed.accepted, attack+'.jsonl');
const profileRendered=flatten(elements.get('profileTableWrap'));
for (const label of ['성공','bench-1','운영 기준','승인 대기 후보 (판정 기준 아님)','이전 기준','저장 hold','일시','학습창 336','0123456789abcdef','취득 순번 7',attack]) {
  // paused false displays 계속; exercise paused failure separately below.
  if (label==='일시') continue;
  assert.ok(profileRendered.includes(label),label);
}
const failure=clone(ready); Object.assign(failure,{command:'recover',ok:false,reason:'storage_write'});
Object.assign(failure.profile,{baseline_ready:false,paused:true,learning:true,learn_count:7});
failure.profile.storage.hold=true;
context.renderRecords(parse(JSON.stringify(failure)).accepted,attack+'.jsonl');
const held=flatten(elements.get('profileTableWrap'));
assert.ok(held.includes('실패')); assert.ok(held.includes('일시 정지')); assert.ok(held.includes('활성 (운영 판정 보류)'));
assert.ok(held.includes('학습 진행 중 (판정 보류)')); assert.ok(held.includes('설비 고장 진단이 아닙니다'));
assert.ok(!flatten(elements.get('recordTableWrap')).includes('storage_write'));
context.renderRecords([], 'empty');
assert.ok(flatten(elements.get('profileTableWrap')).includes('이벤트가 없습니다'));
assert.ok(flatten(elements.get('recordTableWrap')).includes('표시할 측정 기록이 없습니다.'));
(async()=>{
  await elements.get('recordFile').listeners.change({target:{files:[{name:'test.jsonl',text:async()=>JSON.stringify(row())}]}});
  assert.ok(elements.get('importStatus').textContent.includes('형식 확인 1행'));
  assert.equal(elements.get('importErrors').textContent,'');
  console.log('PASS: browser script schema, sequence, uncertainty, original fields, text-only DOM and import handler');
})().catch(error=>{console.error(error);process.exitCode=1;});
