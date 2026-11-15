// Browsing a volume is a view choice. These helpers never select an ingest
// destination, configure CCC or alter the shoot registry.
export function apiPath(base='',path='/api/catalog'){
 if(typeof path!=='string'||!path.startsWith('/api/')||path.includes('..')||path.includes('#'))throw Error('Invalid project API path');
 if(base!==''&&!/^\/primaries\/[A-Za-z0-9_-]+$/.test(base))throw Error('Invalid primary API scope');
 return base+path;
}
export function assetScope(asset={}){
 const apiBase=asset.metadata_base||'';
 apiPath(apiBase,'/api/catalog');
 return {apiBase,contentId:asset.original_content_id||asset.content_id||asset.id,assetId:asset.original_asset_id||asset.id,
  projectId:asset.primary_id?`${asset.primary_id}:${asset.original_project_id||asset.project_id||'project'}`:asset.project_id,
  readOnly:asset.available===false};
}
export function assetForApi(asset){const scope=assetScope(asset);return {...asset,id:scope.assetId,content_id:scope.contentId};}
export function assetOnVolume(asset,primaryId){return !primaryId||asset.primary_id===primaryId;}
export function scopedNotesUrl(base,url){
 apiPath(base,'/api/catalog');
 return typeof url==='string'&&url.startsWith('/notes/')?base+url:url;
}
export function navAsset(asset){return asset.primary_id?{...asset,relative_path:asset.primary_id+'/'+(asset.original_relative_path||asset.relative_path)}:asset;}
export function scopedCatalog(catalog,primaryId){return {...catalog,assets:catalog.assets.filter(a=>assetOnVolume(a,primaryId))};}
export function scopedChatResult(result,catalog,primaryId){
 const asset=catalog.assets.find(a=>a.primary_id===primaryId&&(a.original_asset_id===result.assetId||a.id===result.assetId));
 if(!asset)return null;
 const image=result.image&&result.image.startsWith('/notes/')?(asset.metadata_base||'')+result.image:result.image;
 return {...result,assetId:asset.id,image};
}
export function scopedSelects(ranges,assets){
 const byId=new Map(assets.map(a=>[a.id,a])),rows=ranges.map(range=>({range,asset:byId.get(range.asset_id)}));
 if(!rows.length||rows.some(row=>!row.asset||row.asset.available===false))return null;
 const primary=rows[0].asset.primary_id||'';
 if(rows.some(row=>(row.asset.primary_id||'')!==primary))return null;
 return {apiBase:assetScope(rows[0].asset).apiBase,projectId:assetScope(rows[0].asset).projectId,
  ranges:rows.map(({range,asset})=>({...range,asset_id:asset.original_asset_id||asset.id}))};
}
export function volumeLabel(profile){return profile.label+(profile.active?' · Current ingest':'')+(profile.available===false?' · Offline':'');}
export function backupEntries(primary={}){
 const backup=primary.backup||{},sets=backup.sets||{};
 return (Array.isArray(sets)?sets:Object.entries(sets).map(([label,entry])=>({...entry,label}))).map(entry=>({label:entry.label,current:entry.label===backup.current_set,destinationLabel:entry.destination_label||entry.volume_name||entry.destination_mount||entry.mount||''})).filter(entry=>typeof entry.label==='string').sort((a,b)=>a.label.localeCompare(b.label,undefined,{numeric:true}));
}
