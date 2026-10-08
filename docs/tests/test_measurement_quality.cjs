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
const lines = [row(0),row(1),row(3),row(3),row(2),row(4),row(0,'fedcba9876543210'),legacy(),row(2)];
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
const partial=row(2); partial.samples=3;
Object.assign(partial.acquisition,{end_us:5005000,valid:false,reason:'partial_window'});
assert.equal(context.validateRecord(partial).length,0);
assert.equal(context.validateRecord({...row(), reason:'fg_missing'}).length,0);
const forged=row(); forged.collector_report_transport={status:'contiguous',missing:0};
assert.equal(parse(JSON.stringify(forged)).accepted[0].transport.status,'first');
const attack='<img src=x onerror=alert(1)>';
const xss={...legacy(),reason:attack};
const display=parse([legacy(),row(),no,partial,xss].map(JSON.stringify).join('\n')).accepted;
context.renderRecords(display,attack+'.jsonl');
const flatten = element => element.textContent+' '+element.children.map(flatten).join(' ');
const rendered=flatten(elements.get('recordTableWrap'));
assert.ok(rendered.includes(attack));
assert.ok(rendered.includes('미확인'));
assert.ok(rendered.includes('관측 표본 없음'));
assert.ok(rendered.includes('partial_window'));
assert.ok(rendered.includes('상태 미관측'));
assert.ok(rendered.includes('수신 순번 (파일 내 계산)'));
context.renderRecords([], 'empty');
assert.ok(flatten(elements.get('recordTableWrap')).includes('행이 없습니다'));
(async()=>{
  await elements.get('recordFile').listeners.change({target:{files:[{name:'test.jsonl',text:async()=>JSON.stringify(row())}]}});
  assert.ok(elements.get('importStatus').textContent.includes('형식 확인 1행'));
  assert.equal(elements.get('importErrors').textContent,'');
  console.log('PASS: browser script schema, sequence, uncertainty, original fields, text-only DOM and import handler');
})().catch(error=>{console.error(error);process.exitCode=1;});
