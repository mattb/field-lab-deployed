export function compareEntries(mode){return (a,b)=>
 (mode==='newest'?(b.created_at||0)-(a.created_at||0):mode==='count'?(b.count||0)-(a.count||0):0)||a.name.localeCompare(b.name,undefined,{numeric:true})||a.id.localeCompare(b.id);}
export function folderTree(assets,times={}){
 const root={id:'',name:'Files',children:new Map(),contents:new Set(),created_at:0};
 for(const asset of assets){const pieces=asset.relative_path.split('/');pieces.pop();let node=root;const time=times[asset.id]||0;
  for(const part of ['',...pieces]){if(part){const id=node.id?node.id+'/'+part:part;if(!node.children.has(part))node.children.set(part,{id,name:part,children:new Map(),contents:new Set(),created_at:0});node=node.children.get(part);}node.contents.add(asset.id);node.created_at=Math.max(node.created_at,time);}
 }
 function finish(node){return {...node,count:node.contents.size,contents:[...node.contents],children:[...node.children.values()].map(finish)};}
 return finish(root);
}
export function matchesBrowse(asset,selection,data){
 if(!selection||selection.type==='files')return !selection?.id||asset.relative_path.startsWith(selection.id+'/');
 const entries=selection.type==='tags'?data.tags:selection.type==='people'?data.people:data.faces;
 return !!entries?.find(entry=>entry.id===selection.id)?.contents.includes(asset.content_id||asset.id);
}
// Keep scores in their native ranking spaces; interleave already-gated modalities.
export function mergeGlobalResults(text,images,limit=60){const seen=new Set(),out=[];for(let i=0;i<Math.max(text.length,images.length)&&out.length<limit;i++)for(const item of [text[i],images[i]]){if(!item)continue;const key=item.id+'|'+(item.kind==='image'?'image':'text');if(!seen.has(key)){seen.add(key);out.push(item);}if(out.length===limit)break;}return out;}
