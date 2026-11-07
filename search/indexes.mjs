// Orama owns indexing, lexical matching, vector similarity and hybrid ranking.
import {create,insert,update,search} from '@orama/orama';
export function folderPrefixes(path){const pieces=path.split('/');pieces.pop();return pieces.map((_,i)=>pieces.slice(0,i+1).join('/'));}
const schema={title:'string',path:'string',text:'string',assetKind:'enum',assetId:'enum',interviewId:'string',folders:'enum[]',personIds:'enum[]',speakerIds:'enum[]',faceIds:'enum[]',personNames:'string[]',faceNames:'string[]',source:'enum',recordKind:'enum',identities:'string'};
function record(doc,vector){return {id:doc.id,title:doc.kind==='file'?doc.title:'',path:doc.kind==='file'?doc.path:'',text:doc.text,assetKind:doc.assetKind,assetId:doc.assetId,interviewId:String(doc.assetId),folders:folderPrefixes(doc.path),personIds:doc.personIds||[],speakerIds:doc.speakerIds||[],faceIds:doc.faceIds||[],personNames:doc.personNames||[],faceNames:doc.faceNames||[],identities:[...(doc.personNames||[]),...(doc.faceNames||[]),...(doc.personIds||[]),...(doc.faceIds||[])].join(' '),source:doc.source||'ai',recordKind:doc.kind,...(vector?{embedding:Array.from(vector)}:{})};}
export function createIndexes(documents){const text=create({schema:{...schema,embedding:'vector[384]'}}),images=create({schema:{...schema,embedding:'vector[512]'}});for(const d of documents)insert(text,record(d));return {text,images};}
export function filterWhere(filters={}){return {...(filters.personId?{personIds:{containsAll:[filters.personId]}}:{}),...(filters.faceId?{faceIds:{containsAll:[filters.faceId]}}:{}),...(filters.faceGroups?.length?{and:filters.faceGroups.map(ids=>({or:ids.map(id=>({faceIds:{containsAll:[id]}}))}))}:{}),...(filters.kind&&filters.kind!=='all'?{assetKind:{eq:filters.kind}}:{}),...(filters.folder?{folders:{containsAll:[filters.folder]}}:{}),...(filters.assetId?{assetId:{eq:filters.assetId}}:{})};}
export function addTextVector(db,doc,vector){update(db,doc.id,record(doc,vector));}
export function addImageVector(db,doc,vector){insert(db,record(doc,vector));}
export async function searchText(db,query,filters,vector){const r=await search(db,{mode:vector?'hybrid':'fulltext',term:query,properties:['title','path','text','personNames','faceNames','identities'],boost:{title:2,path:1.3,text:1},tolerance:vector?1:0,exact:!vector,threshold:0,limit:40,where:filterWhere(filters),...(vector?{vector:{property:'embedding',value:Array.from(vector)},similarity:0.12}:{}),includeVectors:false});return r.hits.map(h=>({id:h.id,score:h.score}));}
export async function searchImages(db,vector,filters){const r=await search(db,{mode:'vector',vector:{property:'embedding',value:Array.from(vector)},similarity:0.15,limit:40,where:filterWhere(filters),includeVectors:false});return r.hits.map(h=>({id:h.id,score:h.score,kind:'image'}));}
export async function searchIdentity(db,filters){const r=await search(db,{term:'',limit:2000,where:filterWhere(filters),includeVectors:false});return r.hits.map(h=>({id:h.id,score:h.score}));}

// Research uses the same index and vectors, with native per-interview grouping.
export async function searchResearch(db,query,assetIds,vector,perInterview=6){
 if(!Array.isArray(assetIds)||!assetIds.length)return {rows:[],groups:[]};
 const where={recordKind:{eq:'speech'},assetId:{in:assetIds}};
 const r=await search(db,{mode:vector?'hybrid':'fulltext',term:query,properties:['text'],tolerance:vector?1:0,exact:!vector,threshold:vector?1:0,limit:0,where,groupBy:{properties:['interviewId'],maxResult:Math.max(1,Math.min(12,perInterview))},...(vector?{vector:{property:'embedding',value:Array.from(vector)},similarity:0.12}:{}),includeVectors:false});
 const groups=(r.groups||[]).map(g=>({interview_id:g.values[0],rows:g.result.map(h=>({id:h.id,score:h.score}))}));
 return {rows:groups.flatMap(g=>g.rows),groups};
}
