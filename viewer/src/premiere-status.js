// Only pending data changes and actual receipts drive the visible status.
export function premiereStatus(state,error){
 if(error)return {title:'Premiere status unavailable',next:'Connection lost; retrying automatically',attention:true};
 if(!state)return {title:'Premiere',next:''};
 if(!state.paired)return {title:'Premiere not connected',next:''};
 const count=state.pending?.length||0;
 const attention=!!state.journal?.error||['error','conflict'].includes(state.state);
 const title=attention?'Premiere sync needs attention':state.state==='applied'?'Premiere changes not saved':count?`${count} pending for Premiere`:'Premiere';
 const next=state.journal?.error?'Sync update failed; retrying automatically':attention?'Sync paused until reviewed':count?'Waiting for Premiere panel':'';
 return {title,next,attention,savedAt:state.last_saved_at||state.snapshot_summary?.saved_at||null};
}
