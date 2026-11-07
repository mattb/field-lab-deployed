// The browser and chat use exactly the same Orama schemas, retrieval and gates.
import readline from 'node:readline';
import {createIndexes,addTextVector,addImageVector,searchText,searchImages,searchIdentity,searchResearch} from './indexes.mjs';
import {gateReranked,gateImages,preserveMetadataMatches} from './quality.mjs';
let indexes,documents=[],byId=new Map();
for await(const line of readline.createInterface({input:process.stdin})){
 try{
  const m=JSON.parse(line);let result;
  if(m.action==='init'){
   documents=m.documents;byId=new Map(documents.map(d=>[d.id,d]));indexes=createIndexes(documents);
   for(const r of m.vectors){const d=byId.get(r.document_id);if(!d)continue;if(r.kind==='text'&&d.embed)addTextVector(indexes.text,d,r.vector);else if(r.kind==='image'&&d.kind==='visual')addImageVector(indexes.images,d,r.vector);}
   result={ready:true};
  }else if(m.action==='research_search'){
   result=await searchResearch(indexes.text,m.query,m.asset_ids,m.vector,m.per_interview);
  }else if(m.action==='search'){
   if(m.mode==='images')result={rows:gateImages(await searchImages(indexes.images,m.vector,m.filters),.25)};
   else result={rows:await searchText(indexes.text,m.query,m.filters,m.vector),lexical:await searchText(indexes.text,m.query,m.filters)};
  }else if(m.action==='identity')result={rows:await searchIdentity(indexes.text,m.filters)};
  else if(m.action==='gate')result={rows:preserveMetadataMatches(gateReranked(m.rows,-6),m.lexical,documents,m.query)};
  else throw Error('Unknown search operation');
  if(result.rows)result.rows=result.rows.map(r=>({...byId.get(r.id),...r}));
  process.stdout.write(JSON.stringify(result)+'\n');
 }catch(e){process.stdout.write(JSON.stringify({error:e.message})+'\n');}
}
