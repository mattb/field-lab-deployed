import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeCatalog,buildDocuments} from '../src/data.js';
import {createIndexes,searchText,searchIdentity} from '../src/search-index.js';
test('shared projection retains timed operator metadata and identity facets in the same Orama index',async()=>{
 const document={id:'clip:annotation:1',assetId:'clip',contentId:'sha',title:'clip',path:'day/card/clip.mp4',kind:'operator',assetKind:'video',seconds:12,text:'Pickup at north field tags: evening',personIds:['voice-1'],personNames:['Ariel'],faceIds:[],faceNames:[],source:'operator',embed:true};
 const catalog=normalizeCatalog({search_projection:{documents:[document]},assets:[]});assert.deepEqual(buildDocuments(catalog),[document]);const db=createIndexes(buildDocuments(catalog));assert.equal((await searchText(db.text,'Ariel',{}))[0].id,document.id);assert.equal((await searchText(db.text,'evening',{}))[0].id,document.id);assert.equal((await searchText(db.text,'evening',{personId:'voice-1'})).length,1);assert.equal((await searchText(db.text,'evening',{faceId:'voice-1'})).length,0);
});
import {gateReranked,preserveMetadataMatches} from '../src/search-quality.js';
test('unusual exact operator names/tags survive a poor reranker logit; unrelated AI matches do not',()=>{
 const docs=[{id:'voice',personNames:['Xi']},{id:'note',source:'operator',provenance:{metadata:{tags:['Southeasterly_Pickup_7']}}},{id:'forest',text:'Sunlit forest'}];
 const bad=gateReranked([{id:'voice',rerankScore:-14},{id:'note',rerankScore:-12},{id:'forest',rerankScore:-11}],-6);
 assert.equal(bad.length,0);assert.deepEqual(preserveMetadataMatches(bad,[{id:'voice'}],docs,'Xi').map(r=>r.id),['voice']);assert.deepEqual(preserveMetadataMatches(bad,[{id:'note'}],docs,'Southeasterly_Pickup_7').map(r=>r.id),['note']);assert.deepEqual(preserveMetadataMatches(bad,[{id:'forest'}],docs,'chocolate'),[]);assert.deepEqual(preserveMetadataMatches(bad,[{id:'note'}],docs,'Pickup'),[]);
});
test('asset tags are exact reusable facets in the same Orama index without MLX vectors',async()=>{
 const docs=[{id:'a:tags',assetId:'a',contentId:'a',title:'A',path:'day/a.mp4',kind:'tag',assetKind:'video',seconds:0,text:'Sea ice, Interview',tagIds:['ice','interview'],tagNames:['Sea ice','Interview'],source:'operator_tag',embed:false},{id:'b:tags',assetId:'b',contentId:'b',title:'B',path:'day/b.mp4',kind:'tag',assetKind:'video',seconds:0,text:'Sea ice cream',tagIds:['cream'],tagNames:['Sea ice cream'],source:'operator_tag',embed:false}];
 const db=createIndexes(docs);
 assert.deepEqual((await searchIdentity(db.text,{tagId:'ice'})).map(r=>r.id),['a:tags']);
 assert.deepEqual((await searchIdentity(db.text,{tagIds:['ice','interview']})).map(r=>r.id),['a:tags']);
 assert.deepEqual((await searchIdentity(db.text,{tagIds:['ice','cream']})).map(r=>r.id),[]);
 assert.deepEqual(preserveMetadataMatches([],await searchText(db.text,'Sea ice',{}),docs,'Sea ice').map(r=>r.id),['a:tags']);
 assert.deepEqual(preserveMetadataMatches([],[{id:'a:tags'}],docs,'chocolate'),[]);
});
