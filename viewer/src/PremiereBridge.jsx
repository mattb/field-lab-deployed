import React,{useEffect,useState} from 'react';
import {premiereStatus} from './premiere-status.js';

function conflictValue(value){
 if(value===null||value===undefined)return 'Missing';
 if(typeof value==='string')return value;
 if(value.comments!==undefined)return value.comments;
 return JSON.stringify(value);
}

export function PremiereBridge(){
 const [state,setState]=useState(null),[open,setOpen]=useState(false),[path,setPath]=useState(''),[pair,setPair]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function refresh(signal){
  const response=await fetch('/api/premiere/status',{cache:'no-store',signal});
  const value=await response.json();if(!response.ok)throw Error(value.error||'Premiere status unavailable');
  setState(value);setError('');return value;
 }
 useEffect(()=>{const abort=new AbortController();let timer;
  async function poll(){try{await refresh(abort.signal);}catch(e){if(!abort.signal.aborted)setError(e.message);}finally{if(!abort.signal.aborted)timer=setTimeout(poll,2500);}}
  poll();return()=>{abort.abort();clearTimeout(timer);};
 },[]);
 async function action(name,value={}){setBusy(true);setError('');try{
  const response=await fetch('/api/premiere/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'Premiere request failed');
  if(name==='prepare')setPair(result);await refresh();
 }catch(e){setError(e.message);}finally{setBusy(false);}}
 const display=premiereStatus(state,error);
 const pending=state?.pending?.[0],receipt=state?.last_receipt;
 return <section className="premiere-bridge" aria-label="Premiere synchronization">
  <button className="premiere-status-button" aria-expanded={open} onClick={()=>setOpen(v=>!v)}>
   <strong role="status">{display.title}</strong>
   {display.savedAt&&<span>Last save confirmed {new Date(display.savedAt*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'})}</span>}
   {display.next&&<span>{display.next}</span>}
  </button>
  {open&&<div className="premiere-controls">
   {state?.paired?<><p>{state.native_project_path}</p><p>Saved revision {state.saved_revision} · requested revision {state.desired_revision}</p>
    {state.snapshot_summary&&<p>{state.snapshot_summary.clips} clips · {state.snapshot_summary.tag_bins} tag bins · saved {new Date(state.snapshot_summary.saved_at*1000).toLocaleTimeString()}</p>}
    <button disabled={busy} onClick={()=>action('queue')}>Update Premiere</button>
    {pending&&['error','conflict'].includes(pending.status)&&<><p>{pending.message}</p>{pending.status==='conflict'&&<p>Both versions are retained. Reconcile the note in Premiere before retrying. Retry does not merge or choose a version.</p>}<button disabled={busy} onClick={()=>action('retry',{operation_id:pending.operation_id})}>Retry after review</button></>}
    {pending?.operation_kind==='command'&&['queued','error','conflict'].includes(pending.status)&&<><p>Cancelling keeps any partial output already created in Premiere.</p><button disabled={busy} onClick={()=>action('cancel',{operation_id:pending.operation_id,reason:'Operator cancelled the request and kept Premiere changes'})}>Cancel request; keep Premiere changes</button></>}
    {receipt?.readback?.conflicts?.map((c,i)=><div className="premiere-conflict" key={i}><strong>{c.native?.name||c.requested?.name||c.field||'Premiere edit'}</strong><p>{c.message}</p><p>Premiere: {conflictValue(c.native)}</p><p>Field Lab: {conflictValue(c.requested)}</p></div>)}
    <p>The Premiere panel checks queued requests every 2 seconds while automatic sync is running. Opening Premiere alone does not guarantee the panel is running.</p><p>In Premiere, open Field Lab and choose this Field Lab project folder once. Updates remain queued while Premiere is closed.</p>
    {state.exchange_directory&&<details><summary>Connection details</summary><p>{state.exchange_directory}</p></details>}
   </>:<><label>Premiere project path<input value={path} onChange={e=>setPath(e.target.value)} placeholder="/…/Shoot.prproj"/></label><button disabled={busy||!path.trim()} onClick={()=>action('prepare',{native_project_path:path.trim()})}>Pair Premiere project</button></>}
   {pair&&<p>In Premiere, choose this Field Lab project folder once.</p>}
   {error&&<p role="alert">{error}</p>}
   {state?.journal?.error&&<p role="alert">{state.journal.error}</p>}
  </div>}
 </section>;
}
