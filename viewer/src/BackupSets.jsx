import React,{useEffect,useState} from 'react';
import {backupEntries} from './multivolume.js';

// This control starts an explicitly selected pair. It never changes the ingest
// primary or the desktop Backup pointer; those are cutover operations.
export function BackupSets({shoot,primary}){
 const [state,setState]=useState(null),[error,setError]=useState(''),[sending,setSending]=useState(false);
 useEffect(()=>{const abort=new AbortController();let timer;
  async function poll(){try{const r=await fetch('/api/shoot/backup',{cache:'no-store',signal:abort.signal});const value=await r.json();if(!r.ok)throw Error(value.error||'Backup status unavailable');if(!abort.signal.aborted){setState(value);setError('');}}catch(e){if(!abort.signal.aborted){setState(null);setError(e.message);}}finally{if(!abort.signal.aborted)timer=setTimeout(poll,2500);}}
  poll();return()=>{abort.abort();clearTimeout(timer);};
 },[]);
 const sets=backupEntries(primary),blocked=primary.available===false||Boolean(shoot.pending_cutover)||sending||Boolean(state?.running)||!state;
 async function run(label){if(blocked)return;setSending(true);setError('');try{
  const r=await fetch('/api/shoot/backup',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Lab-CSRF':shoot.csrf_token||''},body:JSON.stringify({primary_id:primary.id,set_label:label})});
  const value=await r.json();if(!r.ok)throw Error(value.error||'CCC could not start');setState(old=>({...old,running:true,primary_id:primary.id,set_label:label,status:'starting',message:'Starting CCC…',error:null}));
 }catch(e){setError(e.message);}finally{setSending(false);}}
 return <details className="backup-sets"><summary>Backups · {primary.label}</summary>
  {primary.available===false&&<p>Connect {primary.label} to run its paired backups.</p>}
  {shoot.pending_cutover&&<p>Finish the pending primary cutover before starting another backup.</p>}
  <p>For a consistent metadata copy, finish indexing and edits, stop AI Indexer, then start this volume’s task in CCC. Desktop Backup runs the active ingest volume’s current task. Run backup here does not pause metadata writers.</p>
  {!sets.length&&<p>No backup sets configured for this volume.</p>}
  {sets.map(set=>{const attempt=state?.attempts?.find(a=>a.primary_id===primary.id&&a.set_label===set.label);return <div className="backup-set" key={set.label}><span>{set.label}{set.current?' · Current backup set':''}{set.destinationLabel?' · '+set.destinationLabel:''}</span><button disabled={blocked||attempt?.needs_history_review} onClick={()=>run(set.label)}>Run backup</button>{attempt&&<p role={attempt.needs_history_review?'alert':'status'}>{attempt.message}</p>}</div>;})}
  {state?.primary_id===primary.id&&state.status&&state.status!=='idle'&&<p role="status">{state.message||(state.running?'CCC is running':'CCC request finished. Review its result and verification in CCC.')}</p>}
  {(error||state?.primary_id===primary.id&&state.error)&&<p role="alert">{error||state.error}</p>}
  <p>CCC copies this volume to the chosen paired disk. This does not change the ingest destination. Review CCC verification before releasing source cards.</p>
 </details>;
}
