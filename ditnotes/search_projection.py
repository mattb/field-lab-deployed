"""One projection for AI evidence, people facets and durable operator metadata.

These records go to the existing Orama text/hybrid index. Image records retain
CLIP512 vectors; speaker enrollment embeddings are never document embeddings.
"""
from __future__ import annotations
from copy import deepcopy
import json


def _chunks(text,size=85):
    words=text.strip().split()
    return [' '.join(words[i:i+size]) for i in range(0,len(words),size)]


# Transcript sentences are ASR timing units, often only a backchannel. Search
# needs short paragraphs instead. These are derived windows, never replacements
# for the original transcript or invented word-level timing.
def _transcript_passages(transcript, *, target_words=100, overlap_words=15, max_words=110, max_gap=8.0):
    import re
    if not 0 <= overlap_words < target_words <= max_words:
        raise ValueError('Invalid transcript passage window')
    streams={}
    for index,sentence in enumerate(transcript):
        text=sentence.get('text','').strip()
        if not text:continue
        start=float(sentence.get('start',0));end=max(start,float(sentence.get('end',start)))
        streams.setdefault(sentence.get('stream'),[]).append((start,end,index,text))
    passages=[]
    filler={'um','uh','oh','yeah','yes','no','okay','ok','right','cool','sure','hmm','mm','thanks','thank','you','great','bye'}
    def emit(block,stream):
        words=[]
        for start,end,index,text in block:
            words.extend((word,start,end,index) for word in text.split())
        offset=0
        while offset<len(words):
            stop=len(words) if len(words)-offset<=max_words else offset+target_words
            window=words[offset:stop];text=' '.join(word[0] for word in window)
            tokens=re.findall(r"[a-z]+",text.casefold())
            # Keep acknowledgements as context when surrounded by speech, but
            # do not give a detached 'Yeah, okay' its own semantic result.
            if not (tokens and len(tokens)<=8 and set(tokens)<=filler):
                passages.append({'text':text,'start':min(w[1] for w in window),'end':max(w[2] for w in window),
                                 'stream':stream,'sentence_indices':list(dict.fromkeys(w[3] for w in window))})
            if stop==len(words):break
            offset=stop-overlap_words
    for stream,sentences in streams.items():
        block=[];last_end=None
        for sentence in sorted(sentences,key=lambda row:(row[0],row[2])):
            if block and sentence[0]-last_end>max_gap:
                emit(block,stream);block=[];last_end=None
            block.append(sentence);last_end=max(last_end if last_end is not None else sentence[1],sentence[1])
        if block:emit(block,stream)
    return sorted(passages,key=lambda p:(p['start'],str(p['stream']),p['sentence_indices'][0]))

def _metadata_text(value):
    if isinstance(value,dict):return ' '.join(str(k)+': '+_metadata_text(v) for k,v in sorted(value.items()))
    if isinstance(value,list):return ' '.join(_metadata_text(v) for v in value)
    if value is None:return ''
    return str(value)

def project_documents(catalog,store):
    names={p['id']:p['name'] for p in store.people() if not p['merged_into']}
    unified=store.unified_people(ensure=False)
    unified_by_id={p['id']:p for p in unified}
    unified_map={(m['modality'],m['evidence_id']):p['id'] for p in unified for m in p['members']}
    names.update({p['id']:p['name'] for p in unified})
    face_entities={f['id']:f for f in store.faces() if not f['merged_into']}
    tags_by_content={}
    for tag in store.asset_tag_assignments():tags_by_content.setdefault(tag['content_id'],[]).append({'id':tag['id'],'name':tag['name']})
    docs=[]
    for asset in catalog.get('assets',[]):
        asset_id=str(asset['id']);content=asset.get('content_id') or asset_id
        tags=tags_by_content.get(content,[])
        observations=store.observations(content)
        faces=store.face_observations(content)
        def face_fields(selected):
            ids=sorted({f['face_id'] for f in selected});linked=sorted({link['person_id'] for f in ids for link in face_entities.get(f,{}).get('voice_links',([face_entities[f]['voice_link']] if face_entities.get(f,{}).get('voice_link') else []))})
            return {'faceIds':ids,'faceNames':[face_entities[f]['name'] for f in ids if face_entities.get(f,{}).get('name')], 'linkedPersonIds':linked,'visiblePersonIds':sorted({unified_map['face',f] for f in ids if ('face',f) in unified_map})}
        def visible(base,selected):
            fields=face_fields(selected);linked=fields.pop('linkedPersonIds');base.update(fields)
            base['personIds']=sorted(set(base['personIds']+linked+fields.get('visiblePersonIds',[])));base['personNames']=[names[p] for p in base['personIds'] if names.get(p)]
            base['identityBasis']='operator_linked_face' if linked else 'visual_face'
            return base
        def base(start=0,end=None,all_people=False,voice_presence=True):
            if not voice_presence:selected=[]
            elif all_people:selected=observations
            elif end is None:selected=[o for o in observations if o['start']<=start<o['end']]
            else:selected=[o for o in observations if o['start']<end and o['end']>start]
            people=sorted({o['person_id'] for o in selected})
            return {'assetId':asset_id,'contentId':content,'title':asset['name'],'path':asset.get('relative_path',asset['name']),
                    'assetKind':asset.get('kind','video'),'identityBasis':'voice','speakerIds':people,'faceIds':[],'faceNames':[],'personIds':sorted(set(people+[unified_map['voice',p] for p in people if ('voice',p) in unified_map])),'heardPersonIds':sorted({unified_map['voice',p] for p in people if ('voice',p) in unified_map}),'visiblePersonIds':[],'personNames':sorted({names[p] for p in people if names.get(p)}),
                    'tagIds':[t['id'] for t in tags],'tagNames':[t['name'] for t in tags]}
        poster=asset.get('poster_url','')
        docs.append(dict(visible(base(all_people=True),faces),id=asset_id+':file',kind='file',text=asset['name']+' '+asset.get('relative_path',''),seconds=0,image=poster,embed=False,source='file'))
        if tags:
            # Labels may come from an operator or an explicitly reviewed AI plan.
            # Their origin remains in the durable edit audit, not inferred here.
            docs.append(dict(base(all_people=True),id=asset_id+':tags',kind='tag',text=', '.join(t['name'] for t in tags),seconds=0,image=poster,embed=False,source='asset_tag'))
        highlights=sorted(asset.get('highlights',[]),key=lambda h:float(h.get('seconds',h.get('start',0))))
        for i,h in enumerate(highlights):
            note=h.get('note',h.get('text','')).strip();start=float(h.get('seconds',h.get('start',0)))
            if not note:continue
            docs.append(dict(visible(base(start,voice_presence=False),[f for f in faces if f['frame_ref']==h.get('image_url',h.get('image','')) or abs(f['seconds']-start)<.02]),id=f'{asset_id}:visual:{i}',kind='visual',text=note,seconds=start,image=h.get('image_url',h.get('image','')),embed=True,source='ai_visual'))
        for face in faces:
            entity=face_entities.get(face['face_id'],{})
            docs.append(dict(visible(base(face['seconds'],voice_presence=False),[face]),id=f'{asset_id}:face:{face["observation_id"]}',kind='face',text=entity.get('name') or ('Unresolved labels: '+', '.join(a['name'] for a in unified_by_id.get(entity.get('unified_person_id'),{}).get('aliases',[])) if entity.get('name_conflict') else face['face_id']),seconds=face['seconds'],image=face['crop_ref'] or face['frame_ref'],embed=False,source='face_observation'))
        present={unified_map['face',f['face_id']] for f in faces if ('face',f['face_id']) in unified_map}|{unified_map['voice',o['person_id']] for o in observations if ('voice',o['person_id']) in unified_map}
        for pid in sorted(present):
            person=unified_by_id[pid]
            if person.get('notes'):
                docs.append(dict(visible(base(all_people=True),faces),id=f'{asset_id}:person-note:{pid}',kind='operator',text=person['notes'],seconds=0,image=poster,embed=True,source='operator_person_note',subjectId=pid))
        transcript=asset.get('transcript',[])
        if not isinstance(transcript,list):transcript=transcript.get('sentences',[])
        for i,passage in enumerate(_transcript_passages(transcript)):
            start,end=passage['start'],passage['end']
            docs.append(dict(base(start,end),id=f'{asset_id}:speech:passage:{i}',kind='speech',text=passage['text'],seconds=start,end=end,stream=passage['stream'],sentence_indices=passage['sentence_indices'],image=poster,embed=True,source='ai_transcript'))
        for annotation in store.annotations(content):
            start=annotation['start'] or 0;end=annotation['end']
            # Only operator-entered values become search prose; provenance remains structured.
            text=annotation['text']
            if annotation['source']=='operator' and annotation['metadata']:
                text+=' '+_metadata_text(annotation['metadata'])
            elif annotation['source'] in ('accepted_inheritance','automatic_inheritance'):
                text+=' '+_metadata_text(annotation['metadata'].get('suggested_metadata',{}))
            for j,chunk in enumerate(_chunks(text)):
                docs.append(dict(base(start,end,annotation['start'] is None),id=f'{asset_id}:annotation:{annotation["id"]}:{j}',kind='operator',text=chunk,seconds=start,end=end,image=poster,embed=True,
                                 source=annotation['source'],annotationId=annotation['id'],provenance={'source_id':annotation['source_id'],'metadata':deepcopy(annotation['metadata'])}))
    return {'schema_version':1,'project_id':store.project_id,'edit_revision':store.edit_revision,'documents':docs}
