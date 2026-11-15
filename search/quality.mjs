// These gates use model-native scores, never Orama's relative hybrid ranking score.
export function gateReranked(rows,minLogit){
 return rows.filter(row=>Number.isFinite(row.rerankScore)&&row.rerankScore>=minLogit).sort((a,b)=>b.rerankScore-a.rerankScore);
}
// Orama vector-only search returns cosine similarity; hybrid/fulltext scores are not valid here.
export function gateImages(rows,minCosine,maxGap=0.02){
 const best=Math.max(-Infinity,...rows.filter(row=>Number.isFinite(row.score)).map(row=>row.score));
 return rows.filter(row=>Number.isFinite(row.score)&&row.score>=minCosine&&row.score>=best-maxGap);
}
// Exact operator labels are facts, not semantic guesses. Orama must first retrieve
// the row lexically; the relevance model cannot veto a complete name/ID/tag.
export function preserveMetadataMatches(gated,lexical,documents,query){
 const normalize=value=>String(value??'').normalize('NFKC').trim().toLocaleLowerCase();
 const wanted=normalize(query),byId=new Map(documents.map(d=>[d.id,d]));
 const rows=new Map(gated.map(r=>[r.id,r]));
 if(!wanted)return [...rows.values()];
 for(const row of lexical){const d=byId.get(row.id);if(!d)continue;
  const tags=d.source==='operator'?d.provenance?.metadata?.tags||[]:d.source==='accepted_inheritance'?d.provenance?.metadata?.suggested_metadata?.tags||[]:[];
  const labels=[...(d.personNames||[]),...(d.faceNames||[]),...(d.personIds||[]),...(d.faceIds||[]),...(Array.isArray(tags)?tags:[tags]),...(['operator_tag','asset_tag'].includes(d.source)?d.tagNames||[]:[])];
  if(labels.some(label=>normalize(label)===wanted)&&!rows.has(row.id))rows.set(row.id,{...row,authoritativeMetadata:true});
 }
 return [...rows.values()];
}
