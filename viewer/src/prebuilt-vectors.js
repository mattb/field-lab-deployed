// Vectors are bound to known documents, the sealed catalog and an exact backend
// model revision. Equal dimensions never imply MLX/ONNX compatibility.
function vectors(payload,documents,expected){
 const result={text:new Map(),image:new Map()},docs=new Map(documents.map(doc=>[doc.id,doc]));
 for(const row of payload?.vectors||[]){
  const kind=row.kind,doc=docs.get(row.document_id),model=kind==='text'?'text':kind==='image'?'clip':null;
  if(!model||!doc||!expected[model]||payload.revision?.[model]!==expected[model])continue;
  if(kind==='text'&&!doc.embed||kind==='image'&&(doc.kind!=='visual'||!doc.image))continue;
  const vector=row.vector,width=kind==='text'?384:512;
  if(!Array.isArray(vector)||vector.length!==width||vector.some(value=>!Number.isFinite(value)))continue;
  const norm=Math.hypot(...vector);if(!norm)continue;
  result[kind].set(doc.id,Float32Array.from(vector,value=>value/norm));
 }
 return result;
}
export function selectPrebuilt(payload,readiness,documents,catalogRevision){
 const empty={text:new Map(),image:new Map()};
 if(readiness.backend?.type!=='mlx'||!readiness.backend.ready)return empty;
 if(catalogRevision!=null&&payload?.catalog_revision!=null&&String(catalogRevision)!==String(payload.catalog_revision))return empty;
 return vectors(payload,documents,readiness.backend.revision||{});
}
export const STATIC_VECTOR_FORMAT='fieldlab-transformers-q8-v1';
export function selectStaticPrebuilt(payload,readiness,documents,catalogRevision){
 const empty={text:new Map(),image:new Map()},models=readiness.models||{};
 if(payload?.backend!=='wasm'||payload?.format!==STATIC_VECTOR_FORMAT||!catalogRevision||payload.catalog_revision!==catalogRevision)return empty;
 if(readiness.backend?.type==='mlx'||!models.embeddings?.ready||!models.clip?.ready)return empty;
 const expected={text:models.embeddings.revision,clip:models.clip.revision,reranker:models.reranker?.revision};
 if(!expected.text||!expected.clip||!expected.reranker||payload.revision?.reranker!==expected.reranker)return empty;
 return vectors(payload,documents,expected);
}
