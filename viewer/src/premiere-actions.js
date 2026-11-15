export function revealRange(asset,seconds=0){
 const duration=Number(asset?.duration);
 if(!asset?.id||!Number.isFinite(duration)||duration<=0)return null;
 const start=Math.max(0,Math.min(Number.isFinite(seconds)?seconds:0,duration-Math.min(duration,1/24)));
 return {asset_id:asset.id,start,end:duration};
}
export function timedSelects(results,assets){
 const byId=new Map(assets.map(a=>[a.id,a])),seen=new Set(),ranges=[];
 for(const r of results){const asset=byId.get(r.assetId),start=Number(r.seconds),end=Number(r.end);
  if(!asset||r.end==null||!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>asset.duration)continue;
  const key=JSON.stringify([asset.id,start,end]);if(seen.has(key))continue;seen.add(key);ranges.push({asset_id:asset.id,start,end});
 }
 return ranges.slice(0,200);
}

// Success is an explicit per-operation acknowledgement, never an inference from
// a request disappearing from the global pending list.
export function nativeCommandState(operation,selects=false){
 switch(operation?.status){
  case 'saved':return {terminal:true,message:selects?'Selects saved in Premiere — switch to Premiere to view them':'Preview opened and saved — switch to Premiere to view it'};
  case 'cancelled':return {terminal:true,message:'Request cancelled; Premiere changes kept'};
  case 'applied':return {terminal:false,message:'Applied in Premiere; not saved'};
  case 'queued':return {terminal:false,message:'Waiting for Premiere — open the paired project and start Field Lab sync'};
  case 'conflict':return {terminal:false,message:operation.message||'Premiere changes need review'};
  case 'error':return {terminal:false,message:operation.message||'Premiere request needs attention'};
  default:return {terminal:false,message:'Awaiting Premiere confirmation'};
 }
}

export function commandAttemptKey(projectId,kind,payload){
 // Unknown project IDs never share a persistent browser slot.
 return projectId?'fieldlab-premiere-request:'+projectId+':'+kind+':'+JSON.stringify(payload):null;
}
export function loadCommandAttempt(key,storage,createId){
 let id;if(key)try{id=storage?.getItem(key);}catch{}
 id=id||createId();if(key)try{storage?.setItem(key,id);}catch{}
 return {key,id};
}
export function clearCommandAttempt(attempt,storage){
 if(!attempt?.key)return;
 try{if(storage?.getItem(attempt.key)===attempt.id)storage.removeItem(attempt.key);}catch{}
}
