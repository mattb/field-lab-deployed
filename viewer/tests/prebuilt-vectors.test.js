import test from 'node:test';
import assert from 'node:assert/strict';
import {selectPrebuilt} from '../src/prebuilt-vectors.js';
const vector=n=>[1,...Array(n-1).fill(0)];
const docs=[{id:'a:visual:0',kind:'visual',embed:true,image:'/notes/frame.jpg'}];
const readiness={backend:{type:'mlx',ready:true,revision:{text:'t',clip:'c',reranker:'r'}}};
const payload={catalog_revision:'1',revision:{text:'t',clip:'c',reranker:'r'},vectors:[{document_id:'a:visual:0',kind:'text',vector:vector(384)},{document_id:'a:visual:0',kind:'image',vector:vector(512)}]};
test('prebuilt vectors require correct revision, document identity, width and finite values',()=>{
 const valid=selectPrebuilt(payload,readiness,docs,'1');assert.equal(valid.text.size,1);assert.equal(valid.image.size,1);
 assert.equal(selectPrebuilt(payload,readiness,docs,'2').text.size,0);
 assert.equal(selectPrebuilt({...payload,revision:{text:'old',clip:'old'}},readiness,docs,'1').image.size,0);
 assert.equal(selectPrebuilt(payload,readiness,[],'1').text.size,0);
 for(const invalid of [[1],Array(384).fill(0),[NaN,...Array(383).fill(0)]])assert.equal(selectPrebuilt({...payload,vectors:[{document_id:'a:visual:0',kind:'text',vector:invalid}]},readiness,docs,'1').text.size,0);
});
test('actual worker consumes prebuilt vectors without content inference requests',async()=>{
 const oldFetch=globalThis.fetch,oldSelf=globalThis.self;let done;const ready=new Promise(r=>done=r);const calls=[];
 globalThis.fetch=async url=>{calls.push(url);assert.equal(url,'/api/search-vectors');return {ok:true,json:async()=>payload};};
 globalThis.self={postMessage:message=>{if(message.type==='status'&&message.phase==='ready')done();}};
 try{await import('../src/search.worker.js');self.onmessage({data:{type:'init',documents:docs.map(d=>({...d,contentId:'c',assetId:'a',assetKind:'video',path:'a.mp4',title:'',text:'test',seconds:1})),readiness,catalogRevision:'1',projectId:'test'}});await ready;assert.deepEqual(calls,['/api/search-vectors']);}
 finally{globalThis.fetch=oldFetch;globalThis.self=oldSelf;}
});

test('static browser vectors reject MLX snapshots and require exact browser and catalog revisions',async()=>{
 const {selectStaticPrebuilt,STATIC_VECTOR_FORMAT}=await import('../src/prebuilt-vectors.js');
 const ready={models:{embeddings:{ready:true,revision:'onnx-text'},clip:{ready:true,revision:'onnx-clip'},reranker:{ready:true,revision:'onnx-rerank'}}};
 const snapshot={...payload,backend:'wasm',format:STATIC_VECTOR_FORMAT,revision:{text:'onnx-text',clip:'onnx-clip',reranker:'onnx-rerank'}};
 assert.equal(selectStaticPrebuilt(snapshot,ready,docs,'1').text.size,1);
 assert.equal(selectStaticPrebuilt(snapshot,ready,docs,'1').image.size,1);
 for(const bad of [{...snapshot,backend:'mlx'},{...snapshot,format:'other'},{...snapshot,catalog_revision:'2'},{...snapshot,revision:{...snapshot.revision,text:'mlx-same-width'}},{...snapshot,revision:{...snapshot.revision,reranker:'other'}}]){
  const selected=selectStaticPrebuilt(bad,ready,docs,'1');assert.equal(selected.text.size,0);
 }
 assert.equal(selectStaticPrebuilt(snapshot,ready,docs,null).text.size,0);
 assert.equal(selectStaticPrebuilt(snapshot,readiness,docs,'1').text.size,0);
});
