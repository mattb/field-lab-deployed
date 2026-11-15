#!/usr/bin/env node
// Generate export vectors with the SAME pinned ONNX q8 models and preprocessing
// used by the browser. This never calls MLX, a hosted API or a remote model.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {STATIC_VECTOR_FORMAT,selectStaticPrebuilt} from '../viewer/src/prebuilt-vectors.js';
async function main(){
const require=createRequire(import.meta.url);
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const option=name=>{const i=process.argv.indexOf(name);return i<0?null:process.argv[i+1];};
if(!option('--root'))throw Error('Usage: node search/build-static-vectors.mjs --root exported-site');
const root=await fs.realpath(path.resolve(option('--root')));
const read=async name=>JSON.parse(await fs.readFile(path.join(root,name),'utf8'));
const catalog=await read('data/catalog.json'),readiness=await read('data/readiness.json');
const docs=catalog.search_projection?.documents;
if(!Array.isArray(docs)||!catalog.catalog_revision)throw Error('A sealed catalog projection is required');
const models=readiness.models||{};
for(const name of ['embeddings','clip','reranker'])if(!models[name]?.ready||!models[name].revision)throw Error(`Prepared browser model required: ${name}`);
const revision={text:models.embeddings.revision,clip:models.clip.revision,reranker:models.reranker.revision};
const textDocs=docs.filter(d=>d.embed),imageDocs=docs.filter(d=>d.kind==='visual'&&d.image);
try{const previous=await read('data/vectors.json'),valid=selectStaticPrebuilt(previous,readiness,docs,catalog.catalog_revision);if(valid.text.size===textDocs.length&&valid.image.size===imageDocs.length){console.log(JSON.stringify({reused:true,text:valid.text.size,images:valid.image.size}));process.exit(0);}}catch{}
// Reject absent/escaping inputs before starting native inference sessions.
const imageSources=new Map();
for(const doc of imageDocs){
 if(!doc.image.startsWith('/notes/')||doc.image.includes('?')||doc.image.includes('#'))throw Error('Image must be a local exported note');
 const source=path.resolve(root,decodeURIComponent(doc.image.slice(1)));
 if(!source.startsWith(root+path.sep)||!(await fs.stat(source)).isFile()||(await fs.realpath(source))!==source)throw Error('Image must stay inside the export without symlinks');
 imageSources.set(doc.id,source);
}
const entry=require.resolve('@huggingface/transformers',{paths:[path.join(repo,'viewer')]});
const imported=await import(pathToFileURL(entry).href),t=imported.env?imported:imported.default;
t.env.allowRemoteModels=false;t.env.allowLocalModels=true;t.env.localModelPath=path.join(root,'models')+path.sep;t.env.useBrowserCache=false;
// The node ONNX runtime performs the export on CPU. The browser loads these
// same q8 ONNX weights through WASM, not an allegedly interchangeable MLX model.
const options={dtype:'q8',device:'cpu',local_files_only:true,session_options:{intraOpNumThreads:2,interOpNumThreads:1}};
const norm=row=>{const n=Math.hypot(...row);if(!n||row.some(x=>!Number.isFinite(x)))throw Error('Invalid generated vector');return row.map(x=>x/n);};
const vectors=[],started=performance.now();
console.error('Loading pinned ONNX text encoder');
const embed=await t.pipeline('feature-extraction','embeddings',options);
try{for(let offset=0;offset<textDocs.length;offset+=16){
 const batch=textDocs.slice(offset,offset+16),tensor=await embed(batch.map(d=>d.text),{pooling:'mean',normalize:true,truncation:true});
 const rows=tensor.tolist();
 for(let i=0;i<batch.length;i++){if(rows[i]?.length!==384)throw Error('Expected384text dimensions');vectors.push({document_id:batch[i].id,kind:'text',vector:norm(rows[i])});}
 if(offset%160===0)console.error(`Text ${Math.min(offset+16,textDocs.length)}/${textDocs.length}`);
}}finally{await embed.dispose();}
if(imageDocs.length){
 console.error('Loading pinned ONNX CLIP image encoder');
 const processor=await t.AutoProcessor.from_pretrained('clip',{local_files_only:true}),vision=await t.CLIPVisionModelWithProjection.from_pretrained('clip',options);
 try{for(let i=0;i<imageDocs.length;i++){
  const doc=imageDocs[i],source=imageSources.get(doc.id);
  const image=await t.RawImage.read(source),input=await processor(image),{image_embeds}=await vision(input),vector=norm(Array.from(image_embeds.data));
  if(vector.length!==512)throw Error('Expected512image dimensions');vectors.push({document_id:doc.id,kind:'image',vector});
  if(i%20===0)console.error(`Images ${i+1}/${imageDocs.length}`);
 }}finally{await vision.dispose();}
}
const payload={format:STATIC_VECTOR_FORMAT,backend:'wasm',catalog_revision:catalog.catalog_revision,revision,vectors};
const valid=selectStaticPrebuilt(payload,readiness,docs,catalog.catalog_revision);
if(valid.text.size!==textDocs.length||valid.image.size!==imageDocs.length)throw Error('Generated vector snapshot failed validation');
const target=path.join(root,'data/vectors.json'),pending=target+'.pending-'+process.pid;
const handle=await fs.open(pending,'wx');try{await handle.writeFile(JSON.stringify(payload));await handle.sync();}finally{await handle.close();}
await fs.rename(pending,target);const directory=await fs.open(path.dirname(target),'r');try{await directory.sync();}finally{await directory.close();}
console.log(JSON.stringify({text:valid.text.size,images:valid.image.size,seconds:(performance.now()-started)/1000,backend:'pinned ONNX q8 CPU export / WASM browser'}));

}
await main().catch(error=>{console.error(error.message);process.exit(1);});
