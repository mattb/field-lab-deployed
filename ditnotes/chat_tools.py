"""Read-only footage tools. Retrieval shares the browser's native Orama code."""
from __future__ import annotations
import json,re,subprocess,threading
from pathlib import Path
from urllib.parse import unquote
from .catalog_index import read_snapshot

class FootageTools:
    def __init__(self,server,notify=lambda *_:None):
        self.server=server;self.notify=notify;self.pool={};self.shown=[];self.lock=threading.Lock();self.node=None;self.evidence_checks=0;self.inspected=set();self.required_face_groups=[];self.combined_search=None
        pair=read_snapshot(server.project)
        if not pair:raise ValueError('The project search index is not ready')
        self.catalog=pair['catalog'];self.assets={a['id']:a for a in self.catalog['assets']}
        self.documents=self.catalog['search_projection']['documents']
        self.node=subprocess.Popen([str(server.tool_root/'bin/node'),str(server.tool_root/'search/chat-search.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        try:self.call(action='init',documents=self.documents,vectors=pair['vectors']['vectors'])
        except BaseException:self.close();raise
    def call(self,**message):
        with self.lock:
            self.node.stdin.write(json.dumps(message)+'\n');self.node.stdin.flush()
            line=self.node.stdout.readline()
            if not line:raise RuntimeError('Local Orama search stopped')
            result=json.loads(line)
            if result.get('error'):raise ValueError(result['error'])
            return result
    def close(self):
        if self.node and self.node.poll() is None:self.node.terminate();self.node.wait(timeout=3)
    def infer(self,action,values,query=''):
        backend=self.server.search_backend
        return backend.executor.submit(backend._infer,action,values,query).result(timeout=120)
    def compact(self,row):
        state=getattr(self,'identity_state',{})
        entities={f['id']:f for f in state.get('faces',[]) if not f.get('merged_into')}
        ids=row.get('faceIds',[])
        evidence=[{'face_id':key,'name':entities.get(key,{}).get('name'),'name_source':'operator_face_group_label' if entities.get(key,{}).get('name') else None,
                   'appearance_basis':'local_face_embedding_group','voice_link':entities.get(key,{}).get('voice_link')} for key in ids]
        visible=[e['name'] for e in evidence if e['name']] if state else row.get('faceNames',[])
        return {'id':row['id'],'asset_id':row['assetId'],'filename':row['title'],'seconds':row.get('seconds',0),'text':row['text'],
                'kind':row['kind'],'text_source':row.get('source'),'visible_names':visible,'person_names':row.get('personNames',[]),'identity_basis':row.get('identityBasis'),
                'face_identity_evidence':evidence,'identity_note':'Named face groups are operator labels joined to visual observations, not names inferred by the caption model. Group matching can be mistaken; a missing voice link does not invalidate a face label.'}
    def refresh_identity(self):
        self.identity_state=self.server.metadata.state()
        self.evidence_checks=getattr(self,'evidence_checks',0)+1
        return self.identity_state
    def configure_request(self,prompt):
        state=self.server.metadata.state();named={}
        for face in state['faces']:
            if face.get('name') and not face.get('merged_into'):named.setdefault(face['name'].casefold(),[]).append(face['id'])
        self.required_face_groups=[ids for name,ids in named.items() if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',prompt,re.I)]
    def search(self,query,mode='text',person_id=None,folder=None,limit=6):
        if not query.strip() or len(query)>500:raise ValueError('Use a short search query')
        if hasattr(self,'server'):self.refresh_identity()
        identity_filter='faceId' if person_id and person_id.startswith('face_') else 'personId'
        filters={k:v for k,v in {identity_filter:person_id,'folder':folder}.items() if v}
        self.notify('Searching '+mode,{'query':query,'filters':filters})
        vector=self.infer('embed_clip_text' if mode=='images' else 'embed_text',[query])['vectors'][0]
        packet=self.call(action='search',query=query,mode=mode,filters=filters,vector=vector)
        if mode=='text' and packet['rows']:
            rows=packet['rows'][:160];scores=[]
            for offset in range(0,len(rows),40):
                scores.extend(self.infer('rerank',[r['text'] if r['kind']!='file' else r['title']+' '+r['path'] for r in rows[offset:offset+40]],query)['scores'])
            rows=[dict(r,rerankScore=score) for r,score in zip(rows,scores)]
            packet=self.call(action='gate',rows=rows,lexical=packet['lexical'],query=query)
        result=[];seen=set()
        for row in packet['rows']:
            key=(row['contentId'],round(row.get('seconds',0),2),row['kind'])
            if key in seen:continue
            seen.add(key);self.pool[row['id']]=row;result.append(self.compact(row))
            if len(result)>=max(1,min(limit,10)):break
        return result
    def person(self,name):
        self.notify('Resolving person',{'name':name})
        state=self.refresh_identity();needle=name.strip().casefold()
        return {'people':[{'id':p['id'],'name':p['name'],'name_conflict':p['name_conflict'],'face_ids':[m['evidence_id'] for m in p['members'] if m['modality']=='face'],'voice_ids':[m['evidence_id'] for m in p['members'] if m['modality']=='voice']} for p in self.server.metadata.people_review()['people'] if p.get('name') and needle in p['name'].casefold()],
                'voices':[{'id':p['id'],'name':p['name']} for p in state['people'] if p.get('name') and needle in p['name'].casefold()],
                'faces':[{'id':p['id'],'name':p['name'],'name_source':'operator_face_group_label','voice_link':p.get('voice_link')} for p in state['faces'] if p.get('name') and needle in p['name'].casefold()],
                'interpretation':'A unified Person owns reviewed image and audio evidence. These legacy face and voice IDs are evidence groups attached to that Person, not separate named people. Visible face matches prove sampled visual appearances; audio evidence only proves the person was heard. A name-conflict record has no confirmed name until reviewed.'}
    def search_person(self,name):
        identity=self.person(name);rows=[]
        for face in identity['faces']:
            rows.extend(self.call(action='identity',filters={'faceId':face['id']})['rows'])
        # Identity membership is an exact Orama facet, not a semantic-name query.
        # Prefer full timed stills over crops and spread examples across clips.
        unique={}
        for row in rows:
            if row['kind'] not in ('visual','face'):continue
            key=(row['contentId'],round(row.get('seconds',0),2))
            if key not in unique or row['kind']=='visual':unique[key]=row
        ordered=sorted(unique.values(),key=lambda r:(r['path'],r.get('seconds',0)))
        chosen=[];seen=set()
        for row in ordered:
            if row['assetId'] not in seen:chosen.append(row);seen.add(row['assetId'])
        selected={r['id'] for r in chosen}
        candidates=chosen+[r for r in ordered if r['id'] not in selected]
        chosen=[candidates[i] for i in sorted({0,len(candidates)//2,len(candidates)-1})] if len(candidates)>3 else candidates
        for row in chosen:self.pool[row['id']]=row
        self.notify('Finding named face appearances',{'name':name,'matching_frames':len(ordered)})
        result={'name':name,'identity':identity,'matching_sampled_frames':len(ordered),'returned_frames':len(chosen),
                'evidence_scope':'Exact saved face-group membership in sampled frames; embedding grouping is fallible. Inspect frames to describe activities. Not a CLIP search for a name, not voice presence, and not exhaustive video coverage.',
                'results':[self.compact(r) for r in chosen]}
        if not chosen:return result
        # Return real images with identity results in the same framework tool
        # response; do not rely on the small model choosing an extra tool call.
        from pydantic_ai import ToolReturn
        ids=[r['id'] for r in chosen];frames=self.frames(ids)
        return ToolReturn(return_value={**result,'candidate_only':True,'inspected_frames':frames.return_value['frames']},content=frames.content)
    def search_shots(self,people,visual_query=''):
        if not people or len(people)>4:raise ValueError('Supply one to four operator-named people')
        # Co-occurrence is already enforced by facets; embedding "same frame"
        # as a scene would incorrectly reject perfectly good identity matches.
        plain=visual_query.casefold()
        for name in people:plain=plain.replace(name.casefold(),'')
        if set(re.findall(r'[a-z]+',plain))<={'together','same','frame','shot','clip','two','people','persons','person','both','and','in','a','the','with','do','does','doing','action','actions'}:visual_query=''
        groups=[]
        for name in people:
            faces=self.person(name)['faces']
            exact=[f for f in faces if f['name'].casefold()==name.strip().casefold()]
            ids=[f['id'] for f in exact or faces]
            if not ids:return {'candidates':[],'reason':f'No saved face group for {name}'}
            groups.append(ids)
        self.combined_search={'people':people,'scene':visual_query.strip(),'face_groups':groups}
        filters={'faceGroups':groups}
        if visual_query.strip():
            vector=self.infer('embed_clip_text',[visual_query])['vectors'][0]
            rows=self.call(action='search',query=visual_query,mode='images',filters=filters,vector=vector)['rows']
            if not rows:rows=self.call(action='identity',filters=filters)['rows']
        else:rows=self.call(action='identity',filters=filters)['rows']
        unique={}
        for row in rows:
            if row['kind'] not in ('visual','image'):continue
            if not all(set(g)&set(row.get('faceIds',[])) for g in groups):continue
            unique.setdefault((row['contentId'],round(row.get('seconds',0),2)),row)
        chosen=list(unique.values())[:3]
        self.combined_search['candidate_count']=len(unique)
        for row in chosen:self.pool[row['id']]=row
        self.notify('Searching people together',{'people':people,'query':visual_query,'candidates':len(unique)})
        result={'candidate_only':True,'people_required_in_same_sampled_frame':people,'visual_requirement':visual_query,'candidate_count':len(unique),
                'setting_verified':False,'warning':'Identity intersection is checked. CLIP similarity and AI captions do not prove the setting. Inspect these images; publish only if all requested conditions are supported. Otherwise return not_verified with no result IDs.',
                'candidates':[self.compact(r) for r in chosen]}
        if not chosen:return result
        from pydantic_ai import ToolReturn
        frames=self.frames([r['id'] for r in chosen])
        return ToolReturn(return_value=result,content=frames.content)
    def clip(self,asset_id,start=0,end=None):
        if asset_id not in self.assets:raise ValueError('Unknown clip ID')
        a=self.assets[asset_id];end=a['duration'] if end is None else min(float(end),a['duration']);start=max(0,float(start))
        if end<start:raise ValueError('End must follow start')
        state=self.refresh_identity();content=a['content_id'];entities={f['id']:f for f in state['faces']}
        self.notify('Reading clip',{'filename':a['name'],'start':start,'end':end})
        return {'asset_id':asset_id,'filename':a['name'],'duration':a['duration'],
                'transcript':[s for s in a.get('transcript',[]) if s['start']<end and s['end']>start][:80],
                'visual_notes':[{'seconds':h['seconds'],'text':h['note']} for h in a.get('highlights',[]) if start<=h['seconds']<=end],
                'voices_heard':[{k:v for k,v in o.items() if k in ('person_id','start','end','identity_basis')} for o in state['observations'] if o['content_id']==content and o['start']<end and o['end']>start],
                'faces_seen':[{'face_id':o['face_id'],'seconds':o['seconds'],'name':entities.get(o['face_id'],{}).get('name'),'name_source':'operator_face_group_label' if entities.get(o['face_id'],{}).get('name') else None,'appearance_basis':'local_face_embedding_group','bbox':o.get('bbox'),'status':o.get('status')} for o in state['face_observations'] if o['content_id']==content and start<=o['seconds']<=end],
                'operator_notes':[a for a in state['annotations'] if a['content_id']==content]}
    def explain_identity(self,result_id):
        row=self.pool.get(result_id)
        if not row:raise ValueError('Use a previously retrieved result ID')
        state=self.refresh_identity();evidence=self.compact(row)
        content=self.assets[row['assetId']]['content_id'];seconds=row.get('seconds',0)
        evidence['face_observations']=[{k:o.get(k) for k in ('face_id','seconds','bbox','status','frame_ref','crop_ref')} for o in state['face_observations'] if o['content_id']==content and abs(o['seconds']-seconds)<.02]
        self.notify('Checking identity evidence',{'filename':row['title'],'seconds':seconds})
        return evidence
    def frames(self,ids):
        from pydantic_ai import ToolReturn,BinaryContent
        from .server import _open_file
        if hasattr(self,'server'):self.refresh_identity()
        evidence=[];content=[]
        for key in ids[:3]:
            row=self.pool.get(key)
            if not row or not row.get('image','').startswith('/notes/'):raise ValueError('Inspect a result returned by search')
            if row['kind'] not in ('visual','image','face'):raise ValueError('Transcript/file thumbnails are not timed frames. Read the clip transcript or search images to inspect visual evidence.')
            relative='_DIT_NOTES/'+unquote(row['image'][len('/notes/'):])
            with _open_file(self.server.project,relative) as f:data=f.read(4_000_001)
            if len(data)>4_000_000:raise ValueError('Frame is too large')
            detail=self.compact(row);evidence.append(detail);content.extend([f"Frame {key} at {row.get('seconds',0):.2f}s. Face identity evidence: {json.dumps(detail['face_identity_evidence'])}. Caption text is AI-generated; names come from operator face labels, not visual recognition by you. A voice name alone does not identify a visible person.",BinaryContent(data=data,media_type='image/jpeg')])
            if not hasattr(self,'inspected'):self.inspected=set()
            self.inspected.add(key)
        self.notify('Inspecting frames',{'result_ids':ids[:3]})
        return ToolReturn(return_value={'frames':evidence},content=content)
    def present(self,ids):
        if not ids or len(ids)>8:raise ValueError('Show between one and eight results')
        if hasattr(self,'server'):self.refresh_identity()
        rows=[];groups=[]
        for key in dict.fromkeys(ids):
            if key not in self.pool:raise ValueError('Only retrieved results can be opened')
            row=self.pool[key];near=next((g for g in groups if g['row']['contentId']==row['contentId'] and abs(g['row'].get('seconds',0)-row.get('seconds',0))<1),None)
            if near:
                near['ids'].append(key)
                if row['kind'] in ('image','visual') and near['row']['kind'] not in ('image','visual'):near['row']=row
            else:groups.append({'row':row,'ids':[key]})
        for group in groups:
            row=group['row'];rows.append({**self.compact(row),'assetId':row['assetId'],'image':row.get('image',''),'evidence':[self.compact(self.pool[key]) for key in group['ids']]})
        self.shown=rows;self.notify('Showing results',{'count':len(rows)})
        from urllib.parse import quote
        return {'shots':[{'id':r['id'],'filename':r['filename'],'seconds':r['seconds'],'citation_url':'#shot='+quote(r['id'],safe='')+'&t='+str(r['seconds'])} for r in rows]}
    def validate_answer(self,answer):
        if answer.outcome=='not_verified':
            if answer.result_ids:raise ValueError('An unverified search must have no final result IDs. Investigated frames are not matches.')
            return
        if answer.outcome!='matched':
            if answer.result_ids:raise ValueError('Only a matched answer publishes shot results; explanations must use an empty result_ids list')
            return
        from .chat_answer import legacy_unverified
        if legacy_unverified(answer.answer):raise ValueError('A no-match answer cannot also publish matching results')
        if not answer.result_ids:raise ValueError('A matched answer needs verified retrieved result IDs')
        groups=getattr(self,'required_face_groups',[])
        combined=getattr(self,'combined_search',None)
        if len(groups)>1 and not combined:raise ValueError('Use search_shots with all requested people together before claiming a match')
        evidence={e.result_id:e.visible_support.strip() for e in answer.visual_evidence}
        for key in answer.result_ids:
            row=self.pool.get(key)
            if not row:raise ValueError('Only retrieved results can be published')
            if row['kind'] in ('visual','image','face') and key not in getattr(self,'inspected',set()):raise ValueError('Inspect the actual frame before publishing it')
            if groups and (row['kind'] not in ('visual','image','face') or not all(set(g)&set(row.get('faceIds',[])) for g in groups)):
                raise ValueError('This frame does not contain all requested named face groups; separate appearances do not satisfy the request')
            if combined and combined['scene'] and not evidence.get(key):raise ValueError('Supply concrete visible support for the requested setting, or return not_verified with no results')
            if combined and combined['scene'] and row['kind']=='face':raise ValueError('A face crop cannot verify a setting; inspect the full scene frame')
    def commit_answer(self,answer):
        self.validate_answer(answer);self.shown=[]
        if answer.result_ids:self.present(answer.result_ids)
        support={e.result_id:e.visible_support for e in answer.visual_evidence}
        for row in self.shown:row.update(selection='verified_match' if answer.outcome=='matched' else 'reference',visual_support=support.get(row['id']))
