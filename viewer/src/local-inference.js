// Local inference transport. Orama owns indexing and retrieval.
export function createLocalInference(fetcher=fetch){
 async function request(action,fields){
  const response=await fetcher('/api/search-models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...fields})});
  const body=await response.json();
  if(!response.ok)throw new Error(body.error||`Local inference returned ${response.status}`);
  return body;
 }
 async function vectors(action,fields,count,width){
  const {vectors}=await request(action,fields);
  if(!Array.isArray(vectors)||vectors.length!==count||vectors.some(row=>!Array.isArray(row)||row.length!==width||row.some(v=>!Number.isFinite(v))))throw new Error(`Invalid ${action} vectors`);
  return vectors.map(row=>{const norm=Math.hypot(...row);if(!norm)throw new Error(`Empty ${action} vector`);return Float32Array.from(row,v=>v/norm);});
 }
 return {
  text:texts=>vectors('embed_text',{texts},texts.length,384),
  clipText:texts=>vectors('embed_clip_text',{texts},texts.length,512),
  images:urls=>{if(urls.some(url=>typeof url!=='string'||!(/^\/notes\//.test(url)||/^\/primaries\/[A-Za-z0-9_-]+\/notes\//.test(url))||url.startsWith('//')))throw new Error('Only local note images can be embedded');return vectors('embed_images',{urls},urls.length,512);},
  async rerank(query,texts){const all=[];for(let offset=0;offset<texts.length;offset+=40){const batch=texts.slice(offset,offset+40);const {scores}=await request('rerank',{query,texts:batch});if(!Array.isArray(scores)||scores.length!==batch.length||scores.some(s=>!Number.isFinite(s)))throw new Error('Invalid reranking scores');all.push(...scores);}return all;}
 };
}
