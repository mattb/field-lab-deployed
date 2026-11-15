import test from 'node:test';
import assert from 'node:assert/strict';
import {assetScope,assetForApi,apiPath,assetOnVolume,navAsset,scopedCatalog,scopedChatResult,scopedSelects,volumeLabel,backupEntries,scopedNotesUrl} from '../src/multivolume.js';
import {folderTree,matchesBrowse} from '../src/browse.js';
import {buildDocuments,normalizeCatalog} from '../src/data.js';
import {createIndexes,addTextVector,searchText} from '../../search/indexes.mjs';
const a={id:'p1:asset',content_id:'p1:hash',original_asset_id:'asset',original_content_id:'hash',primary_id:'p1',primary_label:'ThunderBlade',metadata_base:'/primaries/p1',available:false,relative_path:'20261115/A001/clip.mp4',original_relative_path:'20261115/A001/clip.mp4',duration:30,name:'clip.mp4',highlights:[],transcript:[{start:1,end:4,text:'Life on station'}]};
const b={...a,id:'p2:asset',content_id:'p2:hash',primary_id:'p2',primary_label:'Annex',metadata_base:'/primaries/p2',available:true};
test('same content and paths on two volumes remain distinct in browsing and document identities',()=>{
 const catalog=normalizeCatalog({project:{id:'shoot'},assets:[a,b]});
 const tree=folderTree(catalog.assets.map(navAsset));assert.deepEqual(tree.children.map(x=>x.id),['p1','p2']);assert.equal(tree.count,2);
 const documents=buildDocuments(catalog);assert.equal(new Set(documents.map(d=>d.id)).size,documents.length);
 assert.equal(matchesBrowse(navAsset(a),{type:'files',id:'p1/20261115'},{}),true);
 assert.equal(matchesBrowse(navAsset(b),{type:'files',id:'p1/20261115'},{}),false);
 assert.equal(scopedCatalog(catalog,'p1').assets[0].id,'p1:asset');
});
test('metadata requests carry original IDs only inside the owning primary API scope',()=>{
 const scope=assetScope(a);assert.equal(scope.apiBase,'/primaries/p1');assert.equal(scope.contentId,'hash');assert.equal(scope.readOnly,true);
 assert.equal(assetForApi(a).id,'asset');assert.equal(assetForApi(a).content_id,'hash');
 assert.equal(apiPath(scope.apiBase,'/api/asset-tags?content_id=hash'),'/primaries/p1/api/asset-tags?content_id=hash');
 assert.equal(assetScope(b).readOnly,false);assert.notEqual(assetScope(a).projectId,assetScope(b).projectId);
 for(const invalid of ['/primaries/../p1','//outside','/primaries/p1/api/other','https://outside'])assert.throws(()=>apiPath(invalid,'/api/catalog'));
 assert.throws(()=>apiPath('/primaries/p1','/media/clip.mp4'));
});
test('volume selection is a pure view and cannot change active ingest or backup ownership',()=>{
 const shoot={active_primary:'p2',profiles:[{id:'p1',label:'ThunderBlade',active:false,available:false},{id:'p2',label:'Annex',active:true,available:true}],backup:{current:'annex-task'}};
 const before=JSON.stringify(shoot);assert.equal(assetOnVolume(a,'p1'),true);assert.equal(assetOnVolume(b,'p1'),false);
 assert.match(volumeLabel(shoot.profiles[0]),/Offline/);assert.match(volumeLabel(shoot.profiles[1]),/Current ingest/);
 assert.equal(JSON.stringify(shoot),before);
});
test('chat results cannot fall through to a different volume with the same original asset ID',()=>{
 const result={id:'speech',assetId:'asset',seconds:4,image:'/notes/frame.jpg'};
 assert.equal(scopedChatResult(result,{assets:[a,b]},'p1').assetId,'p1:asset');
 assert.equal(scopedChatResult(result,{assets:[a,b]},'p1').image,'/primaries/p1/notes/frame.jpg');
 assert.equal(scopedChatResult(result,{assets:[b]},'p1'),null);
});
test('Premiere selects cannot mix volumes or use offline sources',()=>{
 assert.equal(scopedSelects([{asset_id:a.id,start:1,end:3}],[a,b]),null);
 assert.equal(scopedSelects([{asset_id:a.id,start:1,end:3},{asset_id:b.id,start:1,end:3}],[{...a,available:true},b]),null);
 const scoped=scopedSelects([{asset_id:b.id,start:1,end:3}],[a,b]);
 assert.equal(scoped.apiBase,'/primaries/p2');assert.equal(scoped.ranges[0].asset_id,'asset');
});
test('the shared Orama index retrieves namespaced documents from current and offline volumes',async()=>{
 const documents=buildDocuments(normalizeCatalog({assets:[a,b]}));const index=createIndexes(documents);
 const vector=Float32Array.from([1,...Array(383).fill(0)]);
 for(const document of documents.filter(d=>d.embed))addTextVector(index.text,document,vector);
 const rows=await searchText(index.text,'experiences on base',{},vector);
 const ids=new Map(documents.map(document=>[document.id,document.assetId]));assert.ok(rows.some(row=>ids.get(row.id)==='p1:asset'));assert.ok(rows.some(row=>ids.get(row.id)==='p2:asset'));
});

test('worker loads a shoot-wide vector snapshot rather than the active primary snapshot',{timeout:5000},async()=>{
 const documents=buildDocuments(normalizeCatalog({assets:[a,b]})),vector=[1,...Array(383).fill(0)];
 const payload={catalog_revision:'shoot-1',revision:{text:'t',clip:'c',reranker:'r'},vectors:documents.filter(d=>d.embed).map(d=>({document_id:d.id,kind:'text',vector}))};
 const readiness={backend:{type:'mlx',ready:true,revision:payload.revision}};
 const previousFetch=globalThis.fetch,previousSelf=globalThis.self,calls=[];let finish;
 const ready=new Promise(resolve=>finish=resolve);
 globalThis.fetch=async url=>{calls.push(url);assert.equal(url,'/api/shoot/vectors');return {ok:true,json:async()=>payload};};
 globalThis.self={postMessage:message=>{if(message.type==='status'&&message.phase==='ready')finish();}};
 try{await import('../src/search.worker.js');self.onmessage({data:{type:'init',documents,readiness,catalogRevision:'shoot-1',projectId:'shoot',vectorsUrl:'/api/shoot/vectors'}});await ready;assert.deepEqual(calls,['/api/shoot/vectors']);}
 finally{globalThis.fetch=previousFetch;globalThis.self=previousSelf;}
});

test('backup set selection is scoped to a primary even when set labels repeat',()=>{
 const previous={id:'p1',backup:{current_set:'B002',sets:{B001:{task_uuid:'previous-task',destination_label:'Previous disk'}}}};
 const current={id:'p2',backup:{current_set:'B001',sets:{B001:{task_uuid:'annex-task',destination_label:'Annex disk'}}}};
 assert.deepEqual(backupEntries(previous),[{label:'B001',current:false,destinationLabel:'Previous disk'}]);
 assert.deepEqual(backupEntries(current),[{label:'B001',current:true,destinationLabel:'Annex disk'}]);
 assert.equal(previous.backup.sets.B001.task_uuid,'previous-task');assert.equal(current.backup.sets.B001.task_uuid,'annex-task');
});

test('original people preview URLs remain on their owning primary',()=>{
 assert.equal(scopedNotesUrl('/primaries/p1','/notes/faces/crop.jpg'),'/primaries/p1/notes/faces/crop.jpg');
 assert.equal(scopedNotesUrl('/primaries/p1','/primaries/p1/notes/faces/crop.jpg'),'/primaries/p1/notes/faces/crop.jpg');
 assert.equal(scopedNotesUrl('','/notes/faces/crop.jpg'),'/notes/faces/crop.jpg');
});
