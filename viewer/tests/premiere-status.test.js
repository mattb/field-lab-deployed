import test from 'node:test';
import assert from 'node:assert/strict';
import {premiereStatus} from '../src/premiere-status.js';
test('pending requests wait for the panel rather than claiming an imminent native save',()=>{
 const d=premiereStatus({paired:true,state:'queued',pending:[{}],saved_revision:1,last_saved_at:20,journal:{running:true,next_check_at:101}},'',100000);
 assert.equal(d.title,'1 pending for Premiere');assert.equal(d.next,'Waiting for Premiere panel');assert.equal(d.savedAt,20);
});
test('idle polling has no visible activity or countdown',()=>{
 const s={paired:true,state:'saved',saved_revision:1,pending:[],last_saved_at:20,journal:{running:true,next_check_at:102}};
 assert.deepEqual(premiereStatus(s,''),{title:'Premiere',next:'',attention:false,savedAt:20});
});
test('connection failure never shows stale up-to-date status',()=>{
 assert.equal(premiereStatus({paired:true,state:'saved',saved_revision:8},'offline').title,'Premiere status unavailable');
 assert.equal(premiereStatus({paired:true,state:'conflict'},'').next,'Sync paused until reviewed');
});
