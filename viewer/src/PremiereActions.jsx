import React,{useEffect,useRef,useState} from 'react';
import {revealRange,nativeCommandState,commandAttemptKey,loadCommandAttempt,clearCommandAttempt} from './premiere-actions.js';

export function PremiereActions({asset,seconds=0,ranges=null,projectId}){
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[operation,setOperation]=useState(null);
 const attempt=useRef(null),alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(!operation)return;const abort=new AbortController();let timer;
  async function poll(){try{
   const response=await fetch('/api/premiere/operation?operation_id='+encodeURIComponent(operation.id),{signal:abort.signal,cache:'no-store'});
   const value=await response.json();if(!response.ok)throw Error(value.error||'Premiere status unavailable');
   if(abort.signal.aborted)return;
   if(value.operation_id!==operation.id)throw Error('Premiere returned another request');
   const current=nativeCommandState(value,operation.selects);setMessage(current.message);
   if(current.terminal){setOperation(null);return;}
  }catch(e){if(!abort.signal.aborted)setMessage(e.message);}
  if(!abort.signal.aborted)timer=setTimeout(poll,2500);}
  poll();return()=>{abort.abort();clearTimeout(timer);};
 },[operation]);
 async function send(){const selected=ranges||[revealRange(asset,seconds)].filter(Boolean);if(!selected.length||busy||operation)return;
  const kind=ranges?'selects':'reveal',payload={ranges:selected,name:ranges?'Field Lab selects':('Preview · '+asset.name).slice(0,100)},key=commandAttemptKey(projectId,kind,payload);
  let storage;try{storage=localStorage;}catch{}
  const identity=key||JSON.stringify([kind,payload]);
  if(attempt.current?.identity!==identity)attempt.current={...loadCommandAttempt(key,storage,()=>crypto.randomUUID()),identity};
  const submitted=attempt.current;
  setBusy(true);setMessage('');try{
   const response=await fetch('/api/premiere/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,request_id:submitted.id})});
   const value=await response.json();if(!response.ok)throw Error(value.error||'Premiere request failed');
   if(!value.operation_id)throw Error('Premiere did not confirm a request ID');
   // Clear only the acknowledged intent, retaining failed/lost-response IDs for
   // an idempotent retry after navigation, reload or temporary server failure.
   clearCommandAttempt(submitted,storage);if(attempt.current===submitted)attempt.current=null;
   if(alive.current){setOperation({id:value.operation_id,selects:kind==='selects'});setMessage('Queued for Premiere');}
  }catch(e){if(alive.current)setMessage(e.message);}finally{if(alive.current)setBusy(false);}
 }
 return <span className="premiere-action"><button className="text-button" disabled={busy||Boolean(operation)||ranges?.length===0} onClick={send}>{ranges?'Create selects from timed matches':'Open preview in Premiere'}</button>{message&&<span role="status">{message}</span>}</span>;
}
