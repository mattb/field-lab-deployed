// Comma-field editing helpers. Server normalization remains authoritative.
export function tagKey(value){return String(value).normalize('NFKC').trim().replace(/\s+/gu,' ').toLowerCase();}
export function parseTags(value){
 const seen=new Set(),result=[];
 for(const part of String(value).split(',')){
  const name=part.normalize('NFKC').trim().replace(/\s+/gu,' '),key=tagKey(name);
  if(name&&!seen.has(key)){seen.add(key);result.push(name);}
 }
 return result;
}
export function tagNames(tags){return (tags||[]).map(tag=>typeof tag==='string'?tag:tag.name);}
export function formatTags(tags){return tagNames(tags).join(', ');}
export function sameTags(left,right){
 const a=new Set(tagNames(left).map(tagKey)),b=new Set(tagNames(right).map(tagKey));
 return a.size===b.size&&[...a].every(key=>b.has(key));
}
export function activeTag(value,cursor=value.length){
 cursor=Math.max(0,Math.min(String(value).length,Number(cursor)||0));
 const start=value.slice(0,cursor).lastIndexOf(',')+1,next=value.indexOf(',',cursor),end=next<0?value.length:next;
 return {start,end,text:value.slice(start,end).trim()};
}
export function hasActiveTagText(value,cursor){return !!activeTag(value,cursor).text;}
export function tagSuggestions(value,cursor,vocabulary,limit=12){
 const token=activeTag(value,cursor),needle=tagKey(token.text);
 const other=value.slice(0,token.start)+value.slice(token.end),used=new Set(parseTags(other).map(tagKey)),seen=new Set();
 return tagNames(vocabulary).filter(name=>{
  const key=tagKey(name);
  if(!key||seen.has(key)||used.has(key)||key===needle||!key.includes(needle))return false;
  seen.add(key);return true;
 }).sort((a,b)=>a.localeCompare(b)).slice(0,limit);
}
export function completeTag(value,cursor,name){
 const token=activeTag(value,cursor),prefix=value.slice(0,token.start),suffix=value.slice(token.end);
 const replacement=(token.start?' ':'')+name;
 if(!suffix){const next=prefix+replacement+', ';return {value:next,cursor:next.length};}
 const next=prefix+replacement+suffix;let position=prefix.length+replacement.length+1;
 while(next[position]===' ')position++;
 return {value:next,cursor:position};
}
export function savedDraft(current,submitted,tags){return current===submitted?formatTags(tags):current;}

const drafts=new Map();
export function tagDraftKey(projectId,contentId){return 'fieldlab:asset-tags-draft:v1:'+JSON.stringify([projectId||'',contentId]);}
function defaultStorage(){try{return typeof window==='undefined'?null:window.localStorage;}catch{return null;}}
export function readTagDraft(key,storage=defaultStorage()){
 if(!key)return null;
 let value=drafts.get(key);
 if(!value){try{value=JSON.parse(storage?.getItem(key)||'null');}catch{}}
 return value&&typeof value.value==='string'&&Number.isInteger(value.revision)?{...value}:null;
}
export function writeTagDraft(key,draft,storage=defaultStorage()){
 if(!key)return;
 if(draft){drafts.set(key,{...draft});try{storage?.setItem(key,JSON.stringify(draft));}catch{}}
 else{drafts.delete(key);try{storage?.removeItem(key);}catch{}}
}
export function restoreTagDraft(draft,serverTags,revision){
 if(!draft||sameTags(parseTags(draft.value),serverTags))return {value:formatTags(serverTags),conflict:false,keep:false};
 return {value:draft.value,conflict:draft.revision!==revision,keep:true};
}
