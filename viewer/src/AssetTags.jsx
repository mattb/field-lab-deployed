import React,{useEffect,useId,useMemo,useRef,useState} from 'react';
import {formatTags,parseTags,sameTags,savedDraft,tagKey,readTagDraft,writeTagDraft,restoreTagDraft,tagDraftKey} from './asset-tags.js';

import {ReactTags} from 'react-tag-autocomplete';

export function AssetTags({contentId,projectId,onChanged}){
 const [text,setText]=useState(''),[saved,setSaved]=useState([]),[vocabulary,setVocabulary]=useState([]);
 const [loaded,setLoaded]=useState(false),[loading,setLoading]=useState(true),[saving,setSaving]=useState(false);
 const [error,setError]=useState(''),[conflict,setConflict]=useState(false),[status,setStatus]=useState('');
 const root=useRef(null);
 const query=useRef(''),[queryText,setQueryText]=useState('');
 const input=useRef(null),alive=useRef(true),textRef=useRef(''),savedRef=useRef([]),revision=useRef(0);
 const savingRef=useRef(false),resave=useRef(false),blocked=useRef(false),loadRequest=useRef(null);
 const project=useRef(projectId||null);
 const draftKey=()=>project.current?tagDraftKey(project.current,contentId):null;
 const id=useId();
 const options=values=>parseTags(formatTags(values)).map(label=>({value:tagKey(label),label}));
 const selected=useMemo(()=>options(parseTags(text)),[text]);
 const suggestions=useMemo(()=>options(vocabulary).filter(tag=>!selected.some(item=>item.value===tag.value)),[vocabulary,selected]);
 const dirty=loaded&&!sameTags(parseTags([text,queryText].join(', ')),saved);
 function remember(value){const draft=[value,query.current].filter(Boolean).join(', ');writeTagDraft(draftKey(),sameTags(parseTags(draft),savedRef.current)?null:{value:draft,revision:revision.current});}
 function update(value){textRef.current=value;setText(value);setStatus('');remember(value);}
 async function load(replace=true,discardDraft=false){
  loadRequest.current?.abort();const controller=new AbortController();loadRequest.current=controller;
  if(replace){setLoading(true);setError('');}
  try{
   const response=await fetch('/api/asset-tags?content_id='+encodeURIComponent(contentId),{cache:'no-store',signal:controller.signal});
   const data=await response.json();if(!response.ok)throw Error(data.error||'Could not load tags');
   if(!alive.current||controller.signal.aborted)return;
   setVocabulary(data.vocabulary||[]);if(data.project_id)project.current=data.project_id;
   if(replace){
    savedRef.current=data.tags||[];setSaved(savedRef.current);revision.current=data.revision;
    const restored=restoreTagDraft(discardDraft?null:readTagDraft(draftKey()),savedRef.current,revision.current);
    query.current='';setQueryText('');if(input.current)input.current.input.value='';
    textRef.current=restored.value;setText(restored.value);setLoaded(true);blocked.current=restored.conflict;setConflict(restored.conflict);setStatus('');
    if(!restored.keep)writeTagDraft(draftKey(),null);
    if(restored.conflict)setError('Tags changed elsewhere. Your entry is kept; load the saved tags to review them.');
   }
  }catch(reason){if(alive.current&&!controller.signal.aborted&&replace)setError(reason.message);}
  finally{if(alive.current&&!controller.signal.aborted&&replace)setLoading(false);}
 }
 useEffect(()=>{alive.current=true;load();return()=>{alive.current=false;loadRequest.current?.abort();};},[contentId]);
 useEffect(()=>{if(!loaded)return;const timer=setInterval(()=>void load(false),10000);return()=>clearInterval(timer);},[loaded,contentId]);
 async function save(){
  if(!loaded||blocked.current)return;
  if(savingRef.current){resave.current=true;return;}
  if(query.current.trim()){const next=formatTags(parseTags([textRef.current,query.current].join(', ')));query.current='';setQueryText('');if(input.current)input.current.input.value='';update(next);}
  const submitted=textRef.current,tags=parseTags(submitted);
  if(sameTags(tags,savedRef.current))return;
  savingRef.current=true;setSaving(true);setError('');setStatus('Saving…');
  try{
   const response=await fetch('/api/asset-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content_id:contentId,tags,base_revision:revision.current})});
   const data=await response.json();
   if(!response.ok){
    if(response.status===409){blocked.current=true;if(alive.current)setConflict(true);}
    throw Error(response.status===409?'Tags changed elsewhere. Your entry is kept; load the saved tags to review them.':data.error||'Could not save tags');
   }
   savedRef.current=data.tags||[];revision.current=data.revision;
   if(!alive.current){
    const pending=readTagDraft(draftKey());
    if(pending){
     const restored=restoreTagDraft(pending,savedRef.current,revision.current);
     if(!restored.keep)writeTagDraft(draftKey(),null);
    }
    return;
   }
   setSaved(savedRef.current);setVocabulary(data.vocabulary||[]);
   const next=savedDraft(textRef.current,submitted,savedRef.current);textRef.current=next;setText(next);remember(next);
   setStatus(sameTags(parseTags([next,query.current].join(', ')),savedRef.current)?'Saved':'');onChanged?.();
  }catch(reason){if(alive.current){setError(reason.message);setStatus('');}}
  finally{
   savingRef.current=false;if(alive.current){setSaving(false);if(resave.current&&!blocked.current){resave.current=false;void save();}}
  }
 }
 function changeTags(names,clearQuery=true){if(!clearQuery&&query.current.trim())names=[...names,...parseTags(query.current)];query.current='';setQueryText('');if(input.current)input.current.input.value='';update(formatTags(parseTags(names.join(', '))));void save();}
 return <form ref={root} className="asset-tags" onSubmit={event=>{event.preventDefault();void save();}}>
  <ReactTags id={id} ref={input} labelText="Tags" selected={selected} suggestions={suggestions}
   allowNew activateFirstOption newOptionPosition="first" delimiterKeys={['Enter',',']} allowResize={false} isDisabled={loading||!loaded}
   placeholderText={loading?'Loading…':'Add tags'} newOptionText='Add "%value%"' noOptionsText="No matching tags"
   onValidate={value=>!!tagKey(value)&&!parseTags(textRef.current).some(name=>tagKey(name)===tagKey(value))}
   onAdd={tag=>changeTags([...parseTags(textRef.current),tag.label])}
   onDelete={index=>changeTags(parseTags(textRef.current).filter((_,at)=>at!==index),false)}
   onInput={value=>{query.current=value;setQueryText(value);setStatus('');remember(textRef.current);}}
   onFocus={()=>{if(loaded)void load(false);}} onBlur={()=>requestAnimationFrame(()=>{if(alive.current&&!root.current?.contains(document.activeElement))void save();})}
   ariaErrorMessage={error?id+'-error':undefined} isInvalid={!!error}/>
  {dirty&&<button type="submit" disabled={saving||conflict}>Save tags</button>}
  {status&&<span className="asset-tags-status" role="status">{status}</span>}
  {error&&<div className="asset-tags-error" id={id+'-error'} role="alert">{error} {(!loaded||conflict)&&<button type="button" onClick={()=>void load(true,conflict)}>{conflict?'Load saved tags':'Retry'}</button>}</div>}
 </form>;
}
