#!/usr/bin/env node
// Private QA output must be OUTSIDE the exported site. Queries and source prose
// belong to the project, never to a reusable repository or public diagnostics.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {createIndexes,addTextVector,searchText} from './indexes.mjs';
import {gateReranked,preserveMetadataMatches} from './quality.mjs';
import {selectStaticPrebuilt} from '../viewer/src/prebuilt-vectors.js';
const args=Object.fromEntries(process.argv.slice(2).reduce((out,value,i,all)=>{if(value.startsWith('--'))out.push([value.slice(2),all[i+1]]);return out;},[]));
if(!args.root||!args.queries||!args.output)throw Error('Usage: --root exported-site --queries private-queries.json --output private-report.json [--candidates40]');
const root=await fs.realpath(path.resolve(args.root)),output=path.resolve(args.output);
if(output===root||output.startsWith(root+path.sep))throw Error('QA report must stay outside the public site');
const catalog=JSON.parse(await fs.readFile(path.join(root,'data/catalog.json'))),readiness=JSON.parse(await fs.readFile(path.join(root,'data/readiness.json'))),payload=JSON.parse(await fs.readFile(path.join(root,'data/vectors.json')));
const docs=catalog.search_projection.documents,byId=new Map(docs.map(d=>[d.id,d])),vectors=selectStaticPrebuilt(payload,readiness,docs,catalog.catalog_revision),indexes=createIndexes(docs);
for(const [id,vector] of vectors.text)addTextVector(indexes.text,byId.get(id),vector);
const require=createRequire(import.meta.url),repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),module=await import(pathToFileURL(require.resolve('@huggingface/transformers',{paths:[path.join(repo,'viewer')]})).href),t=module.env?module:module.default;
t.env.allowRemoteModels=false;t.env.allowLocalModels=true;t.env.localModelPath=path.join(root,'models')+path.sep;t.env.useBrowserCache=false;
const options={dtype:'q8',device:'cpu',local_files_only:true,session_options:{intraOpNumThreads:2,interOpNumThreads:1}};
const embed=await t.pipeline('feature-extraction','embeddings',options),tokenizer=await t.AutoTokenizer.from_pretrained('reranker',{local_files_only:true}),reranker=await t.AutoModelForSequenceClassification.from_pretrained('reranker',options),queries=JSON.parse(await fs.readFile(args.queries,'utf8')),checks=[];
try{for(const input of queries){
 const query=typeof input==='string'?input:input.query,started=performance.now(),tensor=await embed(query,{pooling:'mean',normalize:true,truncation:true}),vector=Array.from(tensor.data);
 const rows=await searchText(indexes.text,query,{},vector),lexical=await searchText(indexes.text,query,{}),candidates=rows.slice(0,Number(args.candidates)||40),scored=[];
 for(let offset=0;offset<candidates.length;offset+=16){const batch=candidates.slice(offset,offset+16),texts=batch.map(r=>{const d=byId.get(r.id);return d.kind==='file'?`${d.title} ${d.path}`:d.text;}),inputs=tokenizer(texts.map(()=>query),{text_pair:texts,padding:true,truncation:true,max_length:256}),{logits}=await reranker(inputs);for(let i=0;i<batch.length;i++)scored.push({...batch[i],rerankScore:Number(logits.data[i])});}
 const matches=preserveMetadataMatches(gateReranked(scored,-6),lexical,docs,query).map(r=>({...byId.get(r.id),...r}));
 checks.push({query,seconds:(performance.now()-started)/1000,results:matches.slice(0,8).map(({id,assetId,text,seconds,kind,rerankScore})=>({id,assetId,text,seconds,kind,rerankScore})),total:matches.length});
}}finally{await embed.dispose();await reranker.dispose();}
await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify({catalog_revision:catalog.catalog_revision,backend:'pinned ONNX q8; shared Orama; browser-equivalent preprocessing',candidates:Number(args.candidates)||40,checks},null,2));
console.log(JSON.stringify({checks:checks.length,seconds:checks.map(c=>Number(c.seconds.toFixed(3))),matches:checks.map(c=>c.total),report:output}));
