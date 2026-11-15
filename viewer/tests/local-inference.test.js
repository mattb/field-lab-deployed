import test from 'node:test';
import assert from 'node:assert/strict';
import {createLocalInference} from '../src/local-inference.js';
const vector=n=>[1,...Array(n-1).fill(0)];
test('local MLX adapter uses same-origin actions and validates model outputs',async()=>{
 const calls=[];
 const api=createLocalInference(async(url,options)=>{const body=JSON.parse(options.body);calls.push({url,...body});return {ok:true,json:async()=>body.action==='rerank'?{scores:[3,1]}:{vectors:(body.texts||body.urls).map(()=>vector(body.action==='embed_text'?384:512))}};});
 assert.equal((await api.text(['speech']))[0].length,384);await api.clipText(['fire']);await api.images(['/notes/frame.jpg']);assert.deepEqual(await api.rerank('fire',['a','b']),[3,1]);
 assert.deepEqual(calls.map(c=>c.action),['embed_text','embed_clip_text','embed_images','rerank']);assert.ok(calls.every(c=>c.url==='/api/search-models'));
 assert.throws(()=>api.images(['https://outside.example/image.jpg']),/local/);
 const malformed=createLocalInference(async()=>({ok:true,json:async()=>({vectors:[[NaN]]})}));await assert.rejects(malformed.text(['speech']),/Invalid/);
 const unavailable=createLocalInference(async()=>({ok:false,status:503,json:async()=>({error:'MLX unavailable'})}));await assert.rejects(unavailable.text(['speech']),/MLX unavailable/);
});
test('worker MLX path indexes and reranks without loading browser models',async()=>{
 const calls=[],messages=[];let readyResolve,resultResolve,globalResolve;
 const ready=new Promise(resolve=>readyResolve=resolve),result=new Promise(resolve=>resultResolve=resolve);
 const globalResult=new Promise(resolve=>globalResolve=resolve);
 const originalFetch=globalThis.fetch,originalSelf=globalThis.self;
 globalThis.fetch=async(url,options)=>{assert.equal(url,'/api/search-models');const body=JSON.parse(options.body);calls.push(body.action);return {ok:true,json:async()=>body.action==='rerank'?{scores:body.texts.map((_,i)=>10-i)}:{vectors:(body.texts||body.urls).map(()=>vector(body.action==='embed_text'?384:512))}};};
 globalThis.self={postMessage:m=>{messages.push(m);if(m.type==='status'&&m.phase==='ready')readyResolve();if(m.type==='results'&&m.stage==='reranked')resultResolve(m);if(m.type==='results'&&m.id===2&&m.stage==='all'&&m.results.some(r=>r.kind==='image'))globalResolve(m);}};
 try{
  await import('../src/search.worker.js');
  self.onmessage({data:{type:'init',projectId:'test',documents:[{id:'speech',assetId:'a',contentId:'c',assetKind:'video',kind:'speech',title:'',path:'a.mp4',text:'A prairie ecosystem',embed:true,seconds:1},{id:'image',assetId:'a',contentId:'c',assetKind:'video',kind:'visual',title:'',path:'a.mp4',text:'Person',embed:true,seconds:8,image:'/notes/a.jpg'}],readiness:{backend:{type:'mlx',ready:true,revision:{text:'t',clip:'c',reranker:'r'}}}}});
  await ready;
  self.onmessage({data:{type:'query',id:1,query:'prairie',mode:'text'}});
  const found=await result;assert.ok(found.results.length);assert.ok(!messages.some(m=>m.type==='results'&&m.stage==='hybrid'));assert.ok(found.results.every(r=>Number.isFinite(r.rerankScore)));assert.ok(calls.includes('embed_text'));assert.ok(calls.includes('embed_images'));assert.ok(calls.includes('rerank'));assert.ok(messages.some(m=>m.type==='capabilities'&&m.backend==='mlx'));
  self.onmessage({data:{type:'query',id:2,query:'prairie',mode:'all'}});
  const combined=await globalResult;assert.ok(combined.results.some(r=>r.kind==='speech'));assert.ok(combined.results.some(r=>r.kind==='image'));
 }finally{globalThis.fetch=originalFetch;globalThis.self=originalSelf;}
});
test('wide reranking respects bounded local API batches and preserves result order',async()=>{
 const batches=[];const api=createLocalInference(async(url,options)=>{const body=JSON.parse(options.body);batches.push(body.texts.length);return {ok:true,json:async()=>({scores:body.texts.map(Number)})};});
 const input=Array.from({length:160},(_,i)=>String(i));assert.deepEqual(await api.rerank('station life',input),input.map(Number));assert.deepEqual(batches,[40,40,40,40]);
});
