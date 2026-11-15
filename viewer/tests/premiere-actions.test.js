import test from 'node:test';import assert from 'node:assert/strict';
import {revealRange,timedSelects} from '../src/premiere-actions.js';
test('native reveal clamps a source position without fabricating an out of bounds range',()=>{
 assert.deepEqual(revealRange({id:'a',duration:8},2),{asset_id:'a',start:2,end:8});assert.ok(revealRange({id:'a',duration:8},99).start<8);assert.equal(revealRange({id:'a',duration:0}),null);
});
test('selects preserve only actual bounded timed evidence and deduplicate ranges',()=>{
 const rows=[{assetId:'a',seconds:1,end:4},{assetId:'a',seconds:1,end:4},{assetId:'a',seconds:5},{assetId:'a',seconds:7,end:9},{assetId:'missing',seconds:0,end:1}];
 assert.deepEqual(timedSelects(rows,[{id:'a',duration:8}]),[{asset_id:'a',start:1,end:4}]);
});

import {nativeCommandState,commandAttemptKey,loadCommandAttempt,clearCommandAttempt} from '../src/premiere-actions.js';
test('only explicit saved or cancelled state is terminal; missing or applied state cannot claim success',()=>{
 assert.deepEqual(nativeCommandState(null),{terminal:false,message:'Awaiting Premiere confirmation'});
 assert.deepEqual(nativeCommandState({status:'applied'}),{terminal:false,message:'Applied in Premiere; not saved'});
 assert.deepEqual(nativeCommandState({status:'saved'},true),{terminal:true,message:'Selects saved in Premiere — switch to Premiere to view them'});
 assert.deepEqual(nativeCommandState({status:'cancelled'},true),{terminal:true,message:'Request cancelled; Premiere changes kept'});
 assert.equal(nativeCommandState({status:'error',message:'Keep edited sequence'}).message,'Keep edited sequence');
 assert.equal(nativeCommandState({status:'conflict'}).terminal,false);
});
test('lost HTTP acknowledgement keeps the same command intent across retries, scoped by project and kind',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const payload={ranges:[{asset_id:'a',start:1,end:2}],name:'Selects'},key=commandAttemptKey('project-a','selects',payload);
 const first=loadCommandAttempt(key,storage,()=> 'request-1');
 const retry=loadCommandAttempt(key,storage,()=> 'request-2');
 assert.deepEqual(retry,first);
 assert.notEqual(key,commandAttemptKey('project-b','selects',payload));
 assert.notEqual(key,commandAttemptKey('project-a','reveal',payload));
 clearCommandAttempt(first,storage);
 assert.equal(loadCommandAttempt(key,storage,()=> 'intent-after-success').id,'intent-after-success');
 // A stale response cannot clear a newer intent's recovery key.
 clearCommandAttempt(first,storage);
 assert.equal(storage.getItem(key),'intent-after-success');
 assert.equal(commandAttemptKey(null,'selects',payload),null);
});
