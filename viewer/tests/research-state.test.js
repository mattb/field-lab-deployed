import test from 'node:test';
import assert from 'node:assert/strict';
import {researchDraft,draftValues,draftChanged,questionRuns,runningResearch,researchSources,coverageText,resolveResearchSource,researchTimeMs,researchDuration,researchProgress,researchCanAsk} from '../src/research-state.js';
test('question draft preserves notes and follows one question per line',()=>{const q={question:'Compare motives',notes:'Keep nuance',followups:['Disagreements?','Missing evidence?']};const draft=researchDraft(q);assert.equal(draftChanged(draft,q),false);assert.deepEqual(draftValues({...draft,followups:' One\n\n Two '} ).followups,['One','Two']);assert.equal(draftChanged({...draft,question:'Other'},q),true);});
test('runs and sources remain scoped to the selected question and original asset',()=>{const rows=[{id:'a',question_id:'one',status:'running'},{id:'b',question_id:'two',status:'complete'}];assert.deepEqual(questionRuns(rows,'one'),[rows[0]]);assert.equal(runningResearch(rows[0]),true);assert.equal(runningResearch(rows[1]),false);assert.deepEqual(researchSources({results:[{id:'source',asset_id:'original',seconds:12}]}),[{id:'source',asset_id:'original',assetId:'original',seconds:12}]);});
test('coverage distinguishes pending footage from searched material',()=>{assert.equal(coverageText({ready:9,total:28,pending:19}),'9/28 interviews ready · 19 pending');assert.equal(coverageText({ready:3,total:4,failed:1}),'3/4 interviews ready · 1 pending · 1 failed');});

test('historical sources bind original content while allowing renames',()=>{
 const source={asset_id:'clip',content_id:'original-hash',seconds:12};
 assert.deepEqual(resolveResearchSource(source,[{id:'clip',content_id:'original-hash',relative_path:'new-folder/renamed.mp4'}]),{assetId:'clip',seconds:12});
 assert.throws(()=>resolveResearchSource(source,[{id:'clip',content_id:'replacement-hash'}]),/replaced/);
 assert.throws(()=>resolveResearchSource(source,[]),/no longer/);
 assert.throws(()=>resolveResearchSource({...source,content_id:undefined},[{id:'clip',content_id:'original-hash'}]),/no content identity/);
 assert.throws(()=>resolveResearchSource(source,[{id:'clip'}]),/replaced/);
 assert.throws(()=>resolveResearchSource({...source,seconds:NaN},[{id:'clip',content_id:'original-hash'}]),/timestamp/);
});

test('research progress uses epoch seconds, elapsed time and heartbeat age',()=>{
 const now=1700000400000;
 assert.equal(researchTimeMs(1700000000),1700000000000);
 assert.equal(researchTimeMs(1700000000000),1700000000000);
 assert.equal(researchTimeMs('2023-11-14T22:13:20Z'),1700000000000);
 assert.equal(researchTimeMs(null),null);
 assert.equal(researchDuration(-5000),'0s');assert.equal(researchDuration(3661000),'1h 1m 1s');
 const p=researchProgress({created_at:1700000000,updated_at:1700000390,status:'running',trace:[{stage:'Model reading context',details:{query:'compare experiences',prompt_tokens:10000,processed_tokens:2000}}]},now);
 assert.equal(p.elapsed,'6m 40s');assert.equal(p.updateAge,'10s');assert.equal(p.stage,'Model reading context');assert.equal(p.query,'compare experiences');assert.deepEqual(p.metrics,['2,000/10,000 context tokens']);
 assert.equal(researchProgress({status:'queued'}).elapsed,null);
 assert.deepEqual(researchProgress({trace:[{stage:'Model reading context',prompt_tokens:500,processed_tokens:250}]}).metrics,['250/500 context tokens']);
});

test('Ask respects the single global research job and read-only server capability',()=>{
 assert.deepEqual(researchCanAsk({runs:[]},'one'),{allowed:true,message:''});
 assert.equal(researchCanAsk({runs:[{question_id:'one',status:'running'}]},'one').allowed,false);
 assert.deepEqual(researchCanAsk({runs:[{question_id:'one',status:'queued'}]},'two'),{allowed:false,message:'Another question is being researched.'});
 assert.equal(researchCanAsk({runs:[{question_id:'one',status:'complete'}]},'two').allowed,true);
 assert.match(researchCanAsk({can_run:false,runs:[]},'one').message,/read-only.*still be saved/);
 assert.equal(researchCanAsk({can_run:false,runs:[]},'one').allowed,false);
});
