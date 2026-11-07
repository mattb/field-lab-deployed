import React,{useEffect,useRef,useState} from 'react';
import {ChatMarkdown} from './ChatMarkdown.jsx';
import {stamp,safeLocalUrl} from './data.js';
import {researchDraft,draftValues,draftChanged,questionRuns,runningResearch,researchSources,coverageText,researchProgress,researchTimeMs,researchCanAsk,researchDuration} from './research-state.js';

export function ResearchPanel({projectId,onSelect}){
 const [state,setState]=useState({questions:[],runs:[],coverage:{}}),[selected,setSelected]=useState(''),[draft,setDraft]=useState(researchDraft()),[base,setBase]=useState(null),[history,setHistory]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[now,setNow]=useState(Date.now());
 useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
 const selectedRef=useRef(selected),alive=useRef(true),refresh=useRef(()=>{});selectedRef.current=selected;
 useEffect(()=>{const controller=new AbortController();let timer,request=0;alive.current=true;
  async function poll(){const current=++request;try{const r=await fetch('/api/research',{signal:controller.signal,cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Research unavailable');if(!controller.signal.aborted&&current===request)setState(d);}catch(e){if(!controller.signal.aborted&&current===request)setError(e.message);}finally{if(!controller.signal.aborted&&current===request)timer=setTimeout(poll,1500);}}
  refresh.current=()=>{clearTimeout(timer);poll();};poll();return()=>{alive.current=false;controller.abort();clearTimeout(timer);};
 },[projectId]);
 const question=state.questions.find(q=>q.id===selected),runs=questionRuns(state.runs,selected),active=runs.findLast(runningResearch),lastRun=runs.at(-1),answers=question?.answers||[],answer=history?answers.find(a=>a.id===history):question?.latest_answer||answers.at(-1),sources=researchSources(answer),dirty=draftChanged(draft,base),progress=researchProgress(active,now),askAvailability=researchCanAsk(state,selected);
 function choose(id){selectedRef.current=id;setSelected(id);const chosen=state.questions.find(q=>q.id===id)||null;setBase(chosen);setDraft(researchDraft(chosen));setHistory('');setError('');}
 async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||`Request failed (${r.status})`);return d;}
 async function save(andAsk=false){if(busy||!draft.question.trim()||(andAsk&&!askAvailability.allowed))return;const selection=selectedRef.current;setBusy(true);setError('');try{
   let saved=base;
   if(!base||dirty){const d=await post('/api/research/question',{action:base?'update':'create',...(base?{id:base.id,expected_version:base.version}:{}),...draftValues(draft)});saved=d.question;}
   if(alive.current){setState(s=>({...s,questions:[...s.questions.filter(q=>q.id!==saved.id),saved]}));if(selectedRef.current===selection){selectedRef.current=saved.id;setSelected(saved.id);setBase(saved);setDraft(researchDraft(saved));setHistory('');}}
   if(andAsk){const run=await post('/api/research/ask',{question_id:saved.id,expected_version:saved.version});if(alive.current)setState(s=>({...s,runs:[...s.runs,{id:run.run_id,question_id:saved.id,status:'queued'}]}));}
   if(alive.current)refresh.current();
  }catch(e){if(alive.current)setError(e.message);}finally{if(alive.current)setBusy(false);}}
 async function archive(){if(!question||busy)return;const selection=question.id;setBusy(true);try{await post('/api/research/question',{action:'archive',id:question.id,expected_version:base?.version});if(alive.current){if(selectedRef.current===selection)choose('');refresh.current();}}catch(e){if(alive.current)setError(e.message);}finally{if(alive.current)setBusy(false);}}
 function selectSource(source){try{onSelect(source);setError('');}catch(e){setError(e.message||'This historical source cannot be opened.');}}
 function edit(key,value){setDraft(d=>({...d,[key]:value}));}
 const stale=Boolean(answer&&(answer.stale||question?.stale||dirty));
 return <div className="research-panel">
  <p className="research-coverage" role="status">{coverageText(state.coverage)}</p>
  <div className="research-toolbar"><button onClick={()=>choose('')} disabled={busy}>New question</button><select aria-label="Saved question" value={selected} onChange={e=>choose(e.target.value)}><option value="">New question</option>{state.questions.map(q=><option key={q.id} value={q.id}>{q.question}</option>)}</select></div>
  <form onSubmit={e=>{e.preventDefault();save(true);}}>
   <label className="research-field">Question<textarea value={draft.question} onChange={e=>edit('question',e.target.value)} rows={3} maxLength={8000} disabled={busy}/></label>
   <details className="research-editor"><summary>Notes and follow-up questions</summary>
    <label className="research-field">Notes<textarea value={draft.notes} disabled={busy} onChange={e=>edit('notes',e.target.value)} rows={3}/></label>
    <label className="research-field">Follow-up questions<textarea value={draft.followups} disabled={busy} onChange={e=>edit('followups',e.target.value)} rows={3} placeholder="One per line"/></label>
   </details>
   <div className="research-actions"><button type="submit" disabled={busy||!askAvailability.allowed||!draft.question.trim()}>{busy?'Saving…':active?'Researching…':lastRun&&['error','interrupted'].includes(lastRun.status)?'Retry':'Ask'}</button><button type="button" onClick={()=>save()} disabled={busy||!draft.question.trim()||(!dirty&&!!question)}>Save</button>{question&&<button type="button" onClick={archive} disabled={busy||!!active}>Archive</button>}</div>
  </form>
  {askAvailability.message&&<p role="status">{askAvailability.message}</p>}
  {question&&base&&question.version!==base.version&&<p role="alert">This question changed in another session. Select it again to load the saved version before saving.</p>}
  {error&&<p role="alert">{error}</p>}
  {active&&<div className="research-progress" role="status"><p>{progress.stage}{progress.query?`: ${progress.query}`:''}</p>{!!progress.metrics.length&&<p>{progress.metrics.join(' · ')}</p>}<p>{progress.elapsed&&<>Elapsed {progress.elapsed}</>}{progress.updateAge&&<>{progress.elapsed?' · ':''}<time dateTime={new Date(progress.updatedAt).toISOString()} title={new Date(progress.updatedAt).toLocaleString()}>Last update {progress.updateAge} ago</time></>}</p></div>}
  {!active&&lastRun&&['error','interrupted'].includes(lastRun.status)&&<p role="alert">{lastRun.error||'Research was interrupted. Retry to start a new answer.'}</p>}
  {answer&&<section className="research-answer">
   <div className="research-answer-toolbar"><h3>Answer</h3>{answer.model&&<small>{answer.model}{Number.isFinite(answer.wall_seconds)?` · ${researchDuration(answer.wall_seconds*1000)}`:''}</small>}{answers.length>1&&<select aria-label="Answer history" value={history} onChange={e=>setHistory(e.target.value)}><option value="">Latest answer</option>{answers.map((a,i)=><option key={a.id} value={a.id}>Answer {i+1}{a.model?` · ${a.model}`:''}{a.created_at?` · ${new Date(researchTimeMs(a.created_at)).toLocaleString()}`:''}</option>)}</select>}</div>
   {stale&&<p className="research-stale">{dirty?'The question has unsaved changes.':'The question or indexed footage has changed since this answer.'} Ask again for an updated answer.</p>}
   <ChatMarkdown text={answer.answer||''} results={sources} onSelect={selectSource}/>
   {!!answer.limitations?.length&&<details><summary>Limitations</summary><ul>{answer.limitations.map((x,i)=><li key={i}>{x}</li>)}</ul></details>}
   {!!answer.claims?.length&&<details><summary>Claims and evidence</summary>{answer.claims.map((c,i)=><div className="research-claim" key={i}><ChatMarkdown text={c.text||c.claim||''} results={sources} onSelect={selectSource}/>{(c.evidence_ids||[]).map(id=>{const source=sources.find(s=>s.id===id);return source?<button key={id} className="research-citation" onClick={()=>selectSource(source)}>{source.filename} · {stamp(source.seconds)}</button>:null;})}</div>)}</details>}
   {answer.coverage&&<details><summary>Answer coverage</summary><p>{coverageText(answer.coverage)}</p>{answer.coverage.interviews?.map((r,i)=><p key={r.id||i}>{r.label||r.filename||r.id} · {r.status||'indexed'}</p>)}</details>}
   {!!answer.trace?.length&&<details><summary>Research steps</summary><ol>{answer.trace.map((s,i)=><li key={i}>{s.stage}{s.query?`: ${s.query}`:''}</li>)}</ol></details>}
   {!!sources.length&&<div className="research-sources"><h3>Sources</h3>{sources.map((r,i)=><button className="chat-result" key={`${r.id}-${i}`} onClick={()=>selectSource(r)}>{safeLocalUrl(r.image)&&<img src={safeLocalUrl(r.image)} alt=""/>}<span>{r.filename} · {stamp(r.seconds)}<small>{r.text}</small></span></button>)}</div>}
   {!!answer.followups?.length&&<details><summary>Suggested follow-up questions</summary><ul>{answer.followups.map((q,i)=><li key={i}>{q}</li>)}</ul></details>}
  </section>}
 </div>;
}
