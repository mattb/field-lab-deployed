export const runningResearch = run => ['queued','running'].includes(run?.status);
export function researchDraft(question){return {question:question?.question||'',notes:question?.notes||'',followups:(question?.followups||[]).join('\n')};}
export function draftValues(draft){return {question:draft.question.trim(),notes:draft.notes,followups:draft.followups.split('\n').map(x=>x.trim()).filter(Boolean)};}
export function draftChanged(draft,question){return JSON.stringify(draftValues(draft))!==JSON.stringify(draftValues(researchDraft(question)));}
export function questionRuns(runs,questionId){return (runs||[]).filter(r=>r.question_id===questionId);}
export function researchSources(answer){return (answer?.results||[]).map(r=>({...r,assetId:r.assetId||r.asset_id}));}
export function coverageText(c={}){const ready=c.ready??c.ready_interviews??c.transcribed_interviews??0,total=c.total??c.total_interviews??0,pending=c.pending??Math.max(0,total-ready);return `${ready}/${total} interviews ready${pending?` · ${pending} pending`:''}${c.failed?` · ${c.failed} failed`:''}`;}
// Answer sources belong to a frozen snapshot; a reused asset ID is not enough.
export function resolveResearchSource(source,assets){
 const id=source?.assetId||source?.asset_id,asset=(assets||[]).find(a=>a.id===id);
 if(!source?.content_id)throw Error('This saved source has no content identity. Ask again to create a verifiable source link.');
 if(!asset)throw Error('The original source is no longer in this project. This historical answer cannot open it.');
 if(!asset.content_id||asset.content_id!==source.content_id)throw Error('This file has been replaced since the answer was saved. The historical source will not open the replacement.');
 const seconds=Number(source.seconds);
 if(!Number.isFinite(seconds)||seconds<0)throw Error('This saved source has no valid playback timestamp.');
 return {assetId:asset.id,seconds};
}
export function researchTimeMs(value){
 if(value==null||value==='')return null;
 const numeric=Number(value);
 const ms=Number.isFinite(numeric)?(Math.abs(numeric)<1e12?numeric*1000:numeric):Date.parse(value);
 return Number.isFinite(ms)?ms:null;
}
export function researchDuration(milliseconds){
 const seconds=Math.max(0,Math.floor(milliseconds/1000)),minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60);
 return hours?`${hours}h ${minutes%60}m ${seconds%60}s`:minutes?`${minutes}m ${seconds%60}s`:`${seconds}s`;
}
export function researchProgress(run,now=Date.now()){
 const stage=run?.trace?.at(-1)||{},details={...stage,...(stage.details||{})};
 const started=researchTimeMs(run?.created_at),updated=researchTimeMs(run?.updated_at);
 const tokens=Number(details.processed_tokens),total=Number(details.prompt_tokens),generated=Number(details.generated_tokens??details.generation_tokens),rate=Number(details.tokens_per_second??details.prompt_tps);
 const metrics=[];
 if(Number.isFinite(tokens)&&Number.isFinite(total)&&total>0)metrics.push(`${tokens.toLocaleString()}/${total.toLocaleString()} context tokens`);
 else if(Number.isFinite(total)&&total>0)metrics.push(`${total.toLocaleString()} context tokens`);
 if(Number.isFinite(generated)&&generated>0)metrics.push(`${generated.toLocaleString()} generated tokens`);
 if(Number.isFinite(rate)&&rate>0)metrics.push(`${rate.toFixed(1)} tokens/s`);
 return {stage:stage.stage||(run?.status==='queued'?'Queued':'Researching…'),query:details.query||'',metrics,elapsed:started==null?null:researchDuration(now-started),updateAge:updated==null?null:researchDuration(now-updated),updatedAt:updated};
}
export function researchCanAsk(state,questionId){
 if(state?.can_run===false)return {allowed:false,message:'Research is read-only on this server. Questions and notes can still be saved.'};
 const active=(state?.runs||[]).find(runningResearch);
 if(active)return {allowed:false,message:active.question_id===questionId?'':'Another question is being researched.'};
 return {allowed:true,message:''};
}
