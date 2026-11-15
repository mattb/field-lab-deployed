import React,{useEffect,useRef,useState} from 'react';
import {stamp,safeLocalUrl} from './data.js';
import {apiPath} from './multivolume.js';
import {ChatMarkdown} from './ChatMarkdown.jsx';

export function ChatPanel({projectId,onSelect,onClear,apiBase='',mapResult=value=>value}){
 const [state,setState]=useState({turns:[],sessions:[]}),[session,setSession]=useState(''),[prompt,setPrompt]=useState(''),[error,setError]=useState(''),[sending,setSending]=useState(false);
 const select=useRef(onSelect),clear=useRef(onClear),opened=useRef('');select.current=onSelect;clear.current=onClear;
 const busy=sending||state.turns.some(t=>t.status==='running');
 useEffect(()=>{let alive=true,timer;async function poll(){try{const r=await fetch(apiPath(apiBase,'/api/chat'+(session?'?session_id='+encodeURIComponent(session):'')),{cache:'no-store'});if(!r.ok)throw Error('Chat is unavailable until footage is indexed');const d=await r.json();if(alive){setState(d);setError('');const last=d.turns.at(-1);if(last?.status==='complete'&&opened.current!==last.id){opened.current=last.id;if(last.outcome==='not_verified')clear.current?.();else if(last.results.length)select.current(last.results[0]);}}}catch(e){if(alive)setError(e.message);}if(alive)timer=setTimeout(poll,1000);}poll();return()=>{alive=false;clearTimeout(timer);};},[session,projectId,apiBase]);
 async function submit(e){e.preventDefault();if(!prompt.trim()||busy)return;setSending(true);setError('');try{const r=await fetch(apiPath(apiBase,'/api/chat'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,session_id:session==='new'?null:session||state.session_id||null})});const d=await r.json();if(!r.ok)throw Error(d.error||'Chat request failed');setSession(d.session_id);setPrompt('');setState(s=>({...s,turns:[...s.turns,{id:d.turn_id,prompt,status:'running',answer:'',results:[],trace:[]}]}));}catch(e){setError(e.message);}finally{setSending(false);}}
 function newChat(){setSession('new');setState({sessions:state.sessions,turns:[]});setPrompt('');}
 return <div className="chat-panel">
  <div className="chat-toolbar"><button onClick={newChat} disabled={busy}>New chat</button>{state.sessions.length>0&&<select aria-label="Chat session" value={session||state.session_id||''} onChange={e=>setSession(e.target.value)}>{session==='new'&&<option value="new">New chat</option>}{state.sessions.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select>}</div>
  {state.turns.map(t=><div className="chat-turn" key={t.id}>
   <p className="chat-question">{t.prompt}</p>
   {t.status==='running'&&<p role="status">{t.trace.at(-1)?.stage||'Thinking…'}</p>}
   {t.answer&&<ChatMarkdown text={t.answer} results={t.results.map(mapResult).filter(Boolean)} onSelect={onSelect}/>}
   {t.error&&<p role="alert">{t.error}</p>}
   {!!t.trace.length&&<details><summary>Search steps</summary><ol>{t.trace.map((s,i)=><li key={i}>{s.stage}{s.query?': '+s.query:s.name?': '+s.name:''}</li>)}</ol></details>}
   {t.results.map(mapResult).filter(Boolean).map(r=><button className="chat-result" key={r.id} onClick={()=>onSelect(r)}>{r.image&&<img src={safeLocalUrl(r.image)} alt="Shot result"/>}<span>{r.filename} · {stamp(r.seconds)}<small>{r.text}</small></span></button>)}
  </div>)}
  <form onSubmit={submit}><textarea aria-label="Ask about footage" placeholder="Ask about footage" value={prompt} onChange={e=>setPrompt(e.target.value)} rows={3} maxLength={4000} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter')submit(e);}}/><button type="submit" disabled={busy||!prompt.trim()}>Send</button></form>
  {error&&<p role="alert">{error}</p>}
 </div>;
}
