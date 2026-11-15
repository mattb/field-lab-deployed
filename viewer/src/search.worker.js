import {mergeGlobalResults} from './browse.js';
import {selectPrebuilt,selectStaticPrebuilt} from './prebuilt-vectors.js';
import {gateReranked,gateImages,preserveMetadataMatches} from './search-quality.js';
import {createLocalInference} from './local-inference.js';
import {createIndexes,addTextVector,addImageVector,searchText,searchImages} from './search-index.js';
import {safeLocalUrl} from './data.js';
import {cacheGet,cachePut,contentKey,imageKey} from './cache.js';
let docs=[],byId=new Map(),indexes=null,pending=null,latest=0,working=false,embed=null,reranker=null,rerankTokenizer=null;
let clipText=null,clipVision=null,clipTokenizer=null,clipProcessor=null,RawImage=null;
let projectId='',generation=0,cursor=0,imageCursor=0,embeddingDocs=[],imageDocs=[],cacheHits=0,cacheEnabled=true,announcedReady=false;
// Conservative defaults calibrated on the trailer; raw logits/cosines are not probabilities.
const MIN_RERANK_LOGIT=-6,MIN_CLIP_COSINE=0.25;
let prebuilt={text:new Map(),image:new Map()};
let revisions={},embeddedCount=0,imageCount=0,localInference=null;
const queryCache=new Map(),send=(type,data)=>self.postMessage({type,...data}),pause=()=>new Promise(r=>setTimeout(r,0));
const status=(phase,label,extra={})=>send('status',{phase,label,done:cursor+imageCursor,total:embeddingDocs.length+imageDocs.length,cacheHits,...extra});
function normalized(data){let length=0;for(const x of data)length+=x*x;length=Math.sqrt(length)||1;return Float32Array.from(data,x=>x/length);}
function emitResults(job,rows,stage,started){if(job.id!==latest)return;if(job.global){job.global[job.mode==='images'?'images':'text']=rows;rows=mergeGlobalResults(job.global.text,job.global.images);stage='all';}send('results',{id:job.id,stage,results:rows.map(r=>({...byId.get(r.id),...r})),elapsed:performance.now()-started,partial:job.global?cursor<embeddingDocs.length||imageCursor<imageDocs.length:job.mode==='images'?imageCursor<imageDocs.length:cursor<embeddingDocs.length});}
async function cached(key,size){if(!cacheEnabled)return null;try{const v=await cacheGet(key);if(v?.vector?.length===size){cacheHits++;return Float32Array.from(v.vector);}}catch{cacheEnabled=false;send('warning',{message:'The search index cannot be saved in this browser. It still works for this session.'});}return null;}
async function persist(key,vector){if(cacheEnabled)try{await cachePut(key,{vector:Array.from(vector),saved:Date.now()});}catch{cacheEnabled=false;}}
async function initialize(message){
 const mine=++generation;prebuilt={text:new Map(),image:new Map()};localInference=null;docs=message.documents;byId=new Map(docs.map(d=>[d.id,d]));projectId=message.projectId||'local-project';cursor=0;imageCursor=0;cacheHits=0;latest=0;pending=null;embed=null;reranker=null;clipText=null;clipVision=null;queryCache.clear();announcedReady=false;embeddedCount=0;imageCount=0;
 indexes=createIndexes(docs);embeddingDocs=docs.filter(d=>d.embed);imageDocs=docs.filter(d=>d.kind==='visual'&&safeLocalUrl(d.image));send('lexical-ready',{documents:docs.length});
 const readiness=message.readiness||{},models=readiness.models||readiness,isReady=name=>models[name]?.ready??readiness.ready??false;
 revisions={text:`wasm:${models.embeddings?.revision||'all-MiniLM-L6-v2-local-v1'}`,clip:`wasm:${models.clip?.revision||'clip-vit-base-patch32-local-v1'}`};
 if(readiness.backend?.type==='mlx'&&readiness.backend.ready){
  const revision=readiness.backend.revision||{};
  if(!revision.text||!revision.clip||!revision.reranker){status('lexical-only','Search model revisions missing');return;}
  revisions={text:`mlx:${revision.text}`,clip:`mlx:${revision.clip}`};localInference=createLocalInference();
  working=true;status('loading','Loading index…');
  try{const response=await fetch(safeLocalUrl(message.vectorsUrl)||'/api/search-vectors',{cache:'no-store'});if(response.ok){const payload=await response.json();if(mine!==generation)return;prebuilt=selectPrebuilt(payload,readiness,docs,message.catalogRevision);}}catch{}
  if(mine!==generation)return;working=false;
  // Availability flags share the scheduler; MLX performs all model work server-side.
  embed=true;reranker=true;clipText=true;clipVision=true;
  send('capabilities',{meaning:true,reranking:true,images:true,backend:'mlx'});schedule();return;
 }
 if(message.staticVectorsUrl){
  if(!safeLocalUrl(message.staticVectorsUrl))throw Error('Static vectors must use a same-origin path');
  try{const response=await fetch(message.staticVectorsUrl);if(!response.ok)throw Error(`Search vectors returned ${response.status}`);const payload=await response.json();if(mine!==generation)return;prebuilt=selectStaticPrebuilt(payload,readiness,docs,message.catalogRevision);
   for(const doc of embeddingDocs){const vector=prebuilt.text.get(doc.id);if(vector){addTextVector(indexes.text,doc,vector);embeddedCount++;}}
   for(const doc of imageDocs){const vector=prebuilt.image.get(doc.id);if(vector){addImageVector(indexes.images,doc,vector);imageCount++;}}
   if(embeddedCount===embeddingDocs.length&&imageCount===imageDocs.length){cursor=embeddingDocs.length;imageCursor=imageDocs.length;}else{indexes=createIndexes(docs);embeddedCount=0;imageCount=0;}
   if(!prebuilt.text.size&&embeddingDocs.length)send('warning',{message:'Static vector snapshot is incompatible; rebuilding search locally.'});
  }catch(e){send('warning',{message:`Prepared search vectors unavailable: ${e.message}`});}
 }
 if(!isReady('embeddings')&&!isReady('clip')){announcedReady=true;cursor=embeddingDocs.length;imageCursor=imageDocs.length;status('lexical-only','Keyword search ready; models unavailable',{reason:'missing-models'});return;}
 working=true;status('loading','Loading search models…');
 try{
  const t=await import('@huggingface/transformers');
  t.env.allowRemoteModels=false;t.env.allowLocalModels=true;t.env.localModelPath='/models/';t.env.backends.onnx.wasm.wasmPaths='/runtime/';t.env.backends.onnx.wasm.numThreads=1;t.env.backends.onnx.wasm.proxy=false;t.env.useBrowserCache=false;RawImage=t.RawImage;
  if(isReady('embeddings'))try{embed=await t.pipeline('feature-extraction','embeddings',{dtype:'q8',device:'wasm',local_files_only:true});}catch(e){cursor=embeddingDocs.length;send('warning',{message:`Meaning search unavailable: ${e.message}. Keyword search still works.`});}else cursor=embeddingDocs.length;
  if(mine!==generation)return;
  if(isReady('reranker')&&embed)try{[rerankTokenizer,reranker]=await Promise.all([t.AutoTokenizer.from_pretrained('reranker',{local_files_only:true}),t.AutoModelForSequenceClassification.from_pretrained('reranker',{dtype:'q8',device:'wasm',local_files_only:true})]);}catch(e){send('warning',{message:`Relevance reranking unavailable: ${e.message}. Text and meaning search still work.`});}
  if(isReady('clip'))try{
   status('loading','Loading image model…');
   [clipTokenizer,clipText]=await Promise.all([t.AutoTokenizer.from_pretrained('clip',{local_files_only:true}),t.CLIPTextModelWithProjection.from_pretrained('clip',{dtype:'q8',device:'wasm',local_files_only:true})]);
   if(imageCursor===imageDocs.length){clipVision=true;}else [clipProcessor,clipVision]=await Promise.all([t.AutoProcessor.from_pretrained('clip',{local_files_only:true}),t.CLIPVisionModelWithProjection.from_pretrained('clip',{dtype:'q8',device:'wasm',local_files_only:true})]);
  }catch(e){clipText=null;clipVision=null;imageCursor=imageDocs.length;send('warning',{message:`Image search unavailable: ${e.message}. Your stills remain browsable.`});}else imageCursor=imageDocs.length;
  send('capabilities',{meaning:!!embed,reranking:!!reranker,images:!!clipVision,backend:'wasm'});
 }catch(e){embed=null;clipVision=null;clipText=null;cursor=embeddingDocs.length;imageCursor=imageDocs.length;status('lexical-only','Text search ready · model runtime unavailable',{reason:e.message});}
 finally{working=false;if(mine===generation)schedule();}
}
async function queryVector(job,image=false){const key=`${image?'clip':'text'}:${job.query}`;let vector=queryCache.get(key);if(!vector){if(localInference){vector=(await (image?localInference.clipText([job.query]):localInference.text([job.query])))[0];}else if(image){const inputs=clipTokenizer(job.query,{padding:true,truncation:true,max_length:77});const {text_embeds}=await clipText(inputs);vector=normalized(text_embeds.data);}else{const t=await embed(job.query,{pooling:'mean',normalize:true,truncation:true});vector=Float32Array.from(t.data);}queryCache.set(key,vector);if(queryCache.size>40)queryCache.delete(queryCache.keys().next().value);}return vector;}
async function runQuery(job){
 if(job.mode==='all'){const global={text:[],images:[]};await runQuery({...job,mode:'text',global});if(job.id===latest)await runQuery({...job,mode:'images',global});return;}
 const started=performance.now();
 if(job.mode==='images'){
  if(!clipText||imageCount===0){emitResults(job,[],'images-pending',started);return;}
  try{const vector=await queryVector(job,true);if(job.id!==latest)return;const rows=await searchImages(indexes.images,vector,job.filters);emitResults(job,gateImages(rows,MIN_CLIP_COSINE),'images',started);}catch(e){send('warning',{message:`Image search could not finish: ${e.message}`});emitResults(job,[],'images-unavailable',started);}return;
 }
 const lex=await searchText(indexes.text,job.query,job.filters);if(!embed||!reranker||embeddedCount===0){emitResults(job,lex,'text',started);return;}
 try{
  const vector=await queryVector(job);if(job.id!==latest)return;
  const rows=await searchText(indexes.text,job.query,job.filters,vector);
  if(job.id!==latest)return;
  if(!rows.length){emitResults(job,[],'reranked',started);return;}
  const candidates=rows.slice(0,localInference?160:40),texts=candidates.map(r=>{const d=byId.get(r.id);return d.kind==='file'?`${d.title} ${d.path}`:d.text;});
  let scores;if(localInference){scores=await localInference.rerank(job.query,texts);}else{const inputs=rerankTokenizer(texts.map(()=>job.query),{text_pair:texts,padding:true,truncation:true,max_length:256});const {logits}=await reranker(inputs);scores=logits.data;}if(job.id!==latest)return;
  candidates.forEach((r,i)=>r.rerankScore=Number(scores[i]));emitResults(job,preserveMetadataMatches(gateReranked(candidates,MIN_RERANK_LOGIT),lex,docs,job.query),'reranked',started);
 }catch(e){send('warning',{message:`Meaning search paused: ${e.message}. Showing keyword matches.`});emitResults(job,lex,'text',started);}
}
async function indexTextBatch(){
 const batch=embeddingDocs.slice(cursor,cursor+8),missing=[];
 for(const d of batch){const key=await contentKey(projectId,d,revisions.text),vector=prebuilt.text.get(d.id)||await cached(key,384);if(vector){addTextVector(indexes.text,d,vector);embeddedCount++;}else missing.push({doc:d,key});}
 if(missing.length){const rows=localInference?await localInference.text(missing.map(x=>x.doc.text)):null;const tensor=rows?{data:Float32Array.from(rows.flatMap(r=>Array.from(r))),dims:[rows.length,384]}:await embed(missing.map(x=>x.doc.text),{pooling:'mean',normalize:true,truncation:true}),width=tensor.dims.at(-1);for(let i=0;i<missing.length;i++){const vector=Float32Array.from(tensor.data.slice(i*width,(i+1)*width));addTextVector(indexes.text,missing[i].doc,vector);embeddedCount++;await persist(missing[i].key,vector);}}
 cursor+=batch.length;status('indexing','Indexing text…');
}
async function indexImage(){
 const doc=imageDocs[imageCursor],key=await imageKey(projectId,doc,revisions.clip);let vector=prebuilt.image.get(doc.id)||await cached(key,512);
 try{if(!vector){if(localInference){vector=(await localInference.images([doc.image]))[0];}else{const image=await RawImage.read(doc.image),inputs=await clipProcessor(image),{image_embeds}=await clipVision(inputs);vector=normalized(image_embeds.data);}await persist(key,vector);}addImageVector(indexes.images,doc,vector);imageCount++;}
 catch(e){send('warning',{message:`One still could not be indexed: ${e.message}. Other images remain searchable.`});}
 imageCursor++;status('indexing','Indexing images…');
}
async function schedule(){
 if(working)return;working=true;const mine=generation;
 try{while(mine===generation){
  if(pending){const job=pending;pending=null;await runQuery(job);await pause();continue;}
  if(embed&&cursor<embeddingDocs.length){await indexTextBatch();await pause();continue;}
  if(clipVision&&imageCursor<imageDocs.length){await indexImage();await pause();continue;}
  if(!announcedReady){announcedReady=true;status('ready',embed&&clipVision?'Search ready':embed?'Text search ready':clipVision?'Image search ready':'Keyword search ready');}break;
 }}catch(e){cursor=embeddingDocs.length;imageCursor=imageDocs.length;status('lexical-only','Keyword search ready · model indexing stopped',{reason:e.message});embed=null;clipVision=null;}
 finally{working=false;if(pending)schedule();}
}
self.onmessage=event=>{
 const m=event.data;if(m.type==='init'){initialize(m).catch(e=>status('lexical-only','Keyword search ready',{reason:e.message}));return;}if(m.type==='cancel'){latest=m.id;pending=null;return;}
 if(m.type==='query'&&indexes){latest=m.id;const job={id:m.id,query:m.query,filters:m.filters||{},mode:m.mode||'text'},started=performance.now();
  if(job.mode!=='images'&&!reranker)searchText(indexes.text,job.query,job.filters).then(rows=>emitResults(job.mode==='all'?{...job,global:{text:[],images:[]},mode:'text'}:job,rows,'text',started));
  else if(job.mode==='images')emitResults(job,[],'images-pending',started);
  pending=job;schedule();
 }
};
