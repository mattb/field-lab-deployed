"""Face evidence in the shared project registry; no face retrieval service.

A face embedding is model-native identity evidence. It is never inserted into the
CLIP512 or text384 indexes, even if its dimension happens to be the same.
"""
from __future__ import annotations
import hashlib,json,math,time
from urllib.parse import unquote,urlsplit


def _ref(value):
    if not value:return ''
    if not isinstance(value,str) or len(value)>2000:raise ValueError('Invalid derived image reference')
    parsed=urlsplit(value)
    if parsed.scheme or parsed.netloc:raise ValueError('Face images must remain local')
    path=unquote(parsed.path)
    if path.startswith('/notes/'):path=path[len('/notes/'):]
    elif path.startswith('/'):raise ValueError('Face images must reference generated notes')
    if any(p in ('','..','.') for p in path.split('/')) or '\x00' in path:raise ValueError('Invalid derived image path')
    return value

class FaceStoreMixin:
    def _init_faces(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS faces(id TEXT PRIMARY KEY,name TEXT,merged_into TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS face_observations(
          id TEXT PRIMARY KEY,content_id TEXT NOT NULL,seconds REAL NOT NULL,face_id TEXT NOT NULL,
          model_revision TEXT NOT NULL,dimension INTEGER NOT NULL,embedding TEXT NOT NULL,
          bbox TEXT NOT NULL,frame_ref TEXT NOT NULL,crop_ref TEXT NOT NULL,detection_id TEXT NOT NULL,
          quality REAL NOT NULL,quality_components TEXT NOT NULL,enrolled INTEGER NOT NULL,
          status TEXT NOT NULL,candidates TEXT NOT NULL,created_at REAL NOT NULL,
          FOREIGN KEY(face_id) REFERENCES faces(id));
        CREATE INDEX IF NOT EXISTS faces_by_clip ON face_observations(content_id,seconds);
        CREATE TABLE IF NOT EXISTS face_profiles(face_id TEXT NOT NULL,model_revision TEXT NOT NULL,dimension INTEGER NOT NULL,
          centroid TEXT NOT NULL,samples INTEGER NOT NULL,PRIMARY KEY(face_id,model_revision,dimension));
        CREATE TABLE IF NOT EXISTS face_voice_links(face_id TEXT PRIMARY KEY,person_id TEXT NOT NULL,reason TEXT NOT NULL,linked_at REAL NOT NULL,
          FOREIGN KEY(face_id) REFERENCES faces(id),FOREIGN KEY(person_id) REFERENCES people(id));
        CREATE TABLE IF NOT EXISTS face_feedback(id TEXT PRIMARY KEY,face_a TEXT,face_b TEXT,decision TEXT NOT NULL,details TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS av_link_feedback(face_id TEXT NOT NULL,person_id TEXT NOT NULL,decision TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(face_id,person_id));
        ''')
    def canonical_face(self,face_id):
        seen=set()
        while True:
            if face_id in seen:raise ValueError('Face merge cycle')
            seen.add(face_id);row=self.db.execute('SELECT merged_into FROM faces WHERE id=?',(face_id,)).fetchone()
            if not row:raise KeyError(face_id)
            if not row[0]:return face_id
            face_id=row[0]
    def _new_face(self,name=None):
        from .project_store import _id
        face=_id('face');self.db.execute('INSERT INTO faces VALUES(?,?,NULL,?)',(face,name,time.time()));return face
    def faces(self):
        result=[]
        for row in self.db.execute('SELECT * FROM faces ORDER BY created_at,id'):
            item=dict(row,identity_basis='visual_face');link=self.db.execute('SELECT person_id,reason,linked_at FROM face_voice_links WHERE face_id=?',(self.canonical_face(row['id']),)).fetchone()
            item['voice_link']={'person_id':self.canonical_person(link['person_id']),'reason':link['reason'],'linked_at':link['linked_at'],'source':'operator'} if link else None
            result.append(item)
        return result
    def _refresh_face_profiles(self,face_id):
        from .project_store import unit,_json
        self.db.execute('DELETE FROM face_profiles WHERE face_id=?',(face_id,));groups={}
        for row in self.db.execute('SELECT model_revision,dimension,embedding FROM face_observations WHERE face_id=? AND enrolled=1',(face_id,)):
            groups.setdefault((row['model_revision'],row['dimension']),[]).append(json.loads(row['embedding']))
        for (revision,dimension),vectors in groups.items():
            centroid=unit([sum(v[i] for v in vectors) for i in range(dimension)])
            self.db.execute('INSERT INTO face_profiles VALUES(?,?,?,?,?)',(face_id,revision,dimension,_json(centroid),len(vectors)))
    def assign_face(self,content_id,seconds,embedding,model_revision,quality,*,bbox,frame_ref='',crop_ref='',detection_id='0',
                    quality_components=None,min_quality=.7,match_threshold=.8,threshold=None,margin=.08,auto_assign=False,
                    exclude_face_ids=(),observation_id=None,calibration_id=None,max_enrollments_per_face_per_clip=3):
        from .project_store import _text,_json,unit
        content_id=_text(content_id,128);model_revision=_text(model_revision,1000);detection_id=_text(str(detection_id),200)
        seconds=float(seconds);quality=float(quality);match_threshold=match_threshold if threshold is None else float(threshold)
        if not math.isfinite(seconds) or seconds<0 or not math.isfinite(quality) or not 0<=quality<=1:raise ValueError('Invalid face time or quality')
        if not -1<=match_threshold<=1 or not 0<=margin<=2:raise ValueError('Invalid face matching policy')
        if not isinstance(bbox,(list,tuple)) or len(bbox)!=4:raise ValueError('Face bbox must be normalized x1,y1,x2,y2')
        bbox=[float(x) for x in bbox]
        if any(not math.isfinite(x) or x<0 or x>1 for x in bbox) or bbox[0]>=bbox[2] or bbox[1]>=bbox[3]:raise ValueError('Invalid normalized face bbox')
        frame_ref=_ref(frame_ref);crop_ref=_ref(crop_ref);components=quality_components or {}
        if not isinstance(components,dict):raise ValueError('Face quality components must be an object')
        vector=unit(embedding) if embedding is not None else None;dimension=len(vector) if vector is not None else 0
        identity=[content_id,round(seconds,6),frame_ref,detection_id,model_revision]
        observation_id=observation_id or 'faceobs_'+hashlib.sha256(_json(identity).encode()).hexdigest()
        with self._transaction():
            if vector is not None:
                namespace='face:'+model_revision
                declared=self.db.execute('SELECT dimension FROM embedding_models WHERE revision=?',(namespace,)).fetchone()
                if declared and declared[0]!=dimension:raise ValueError('Face model revision changed embedding dimension')
                self.db.execute('INSERT OR IGNORE INTO embedding_models VALUES(?,?)',(namespace,dimension))
            old=self.db.execute('SELECT * FROM face_observations WHERE id=?',(observation_id,)).fetchone()
            if old:
                if old['content_id']!=content_id or old['model_revision']!=model_revision:raise ValueError('Face observation ID reused for different evidence')
                if json.loads(old['embedding']) is not None or vector is None:return self._face_observation(old)
            excluded={self.canonical_face(x) for x in exclude_face_ids}
            # Two distinct detections in one sampled frame do not auto-collapse.
            for row in self.db.execute('SELECT * FROM face_observations WHERE content_id=? AND model_revision=? AND ABS(seconds-?)<.02',(content_id,model_revision,seconds)):
                if row['id']==observation_id:continue
                if frame_ref and row['frame_ref'] and frame_ref!=row['frame_ref']:continue
                if row['detection_id']!=detection_id or json.loads(row['bbox'])!=bbox:excluded.add(self.canonical_face(row['face_id']))
            clean=vector is not None and quality>=min_quality;candidates=[]
            if clean:
                for row in self.db.execute('SELECT p.* FROM face_profiles p JOIN faces f ON p.face_id=f.id WHERE p.model_revision=? AND p.dimension=? AND f.merged_into IS NULL',(model_revision,dimension)):
                    if row['face_id'] not in excluded:candidates.append({'face_id':row['face_id'],'cosine':sum(a*b for a,b in zip(vector,json.loads(row['centroid'])))})
                candidates.sort(key=lambda x:(-x['cosine'],x['face_id']))
            strong=bool(candidates and candidates[0]['cosine']>=match_threshold and (len(candidates)<2 or candidates[0]['cosine']-candidates[1]['cosine']>=margin))
            if old:face=self.canonical_face(old['face_id']);status='same_detection'
            elif clean and strong and auto_assign:face=candidates[0]['face_id'];status='matched'
            else:face=self._new_face();status='provisional' if clean else 'insufficient_evidence'
            count=self.db.execute('SELECT COUNT(*) FROM face_observations WHERE face_id=? AND content_id=? AND model_revision=? AND enrolled=1',(face,content_id,model_revision)).fetchone()[0]
            enrolled=bool(clean and count<max_enrollments_per_face_per_clip)
            fields=(content_id,seconds,face,model_revision,dimension,_json(vector),_json(bbox),frame_ref,crop_ref,detection_id,quality,_json(components),int(enrolled),status,_json(candidates[:5]))
            if old:
                self.db.execute('UPDATE face_observations SET content_id=?,seconds=?,face_id=?,model_revision=?,dimension=?,embedding=?,bbox=?,frame_ref=?,crop_ref=?,detection_id=?,quality=?,quality_components=?,enrolled=?,status=?,candidates=? WHERE id=?',(*fields,observation_id))
            else:self.db.execute('INSERT INTO face_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(observation_id,*fields,time.time()))
            if enrolled:self._refresh_face_profiles(face)
            self._audit('face_observed',{'observation_id':observation_id,'face_id':face,'status':status,'auto_assign':auto_assign,'policy':{'threshold':match_threshold,'margin':margin,'calibration_id':calibration_id},'quality_is_probability':False})
            return self._face_observation(self.db.execute('SELECT * FROM face_observations WHERE id=?',(observation_id,)).fetchone())
    def _face_observation(self,row):
        result=dict(row)
        for key in ('embedding','bbox','quality_components','candidates'):result[key]=json.loads(result[key])
        result['face_id']=self.canonical_face(result['face_id']);result['observation_id']=result.pop('id');result['identity_basis']='visual_face'
        return result
    def face_observations(self,content_id=None):
        rows=self.db.execute('SELECT * FROM face_observations'+(' WHERE content_id=?' if content_id else '')+' ORDER BY seconds,id',((content_id,) if content_id else ()))
        return [self._face_observation(row) for row in rows]
    def name_face(self,face_id,name,*,sync_linked=True):
        from .project_store import _text
        name=_text(name,200) if name is not None else None
        with self._transaction():
            face_id=self.canonical_face(face_id);old=self.db.execute('SELECT name FROM faces WHERE id=?',(face_id,)).fetchone()[0]
            if old==name:return
            self.db.execute('UPDATE faces SET name=? WHERE id=?',(name,face_id));self._audit('face_named',{'face_id':face_id,'old_name':old,'name':name})
            link=self.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face_id,)).fetchone()
            if name and link and sync_linked:self._sync_linked_name(link[0],name)
    def _sync_linked_name(self,person_id,name):
        person_id=self.canonical_person(person_id)
        old=self.db.execute('SELECT name FROM people WHERE id=?',(person_id,)).fetchone()[0]
        if old!=name:
            self.db.execute('UPDATE people SET name=? WHERE id=?',(name,person_id));self._audit('linked_identity_named',{'person_id':person_id,'name':name,'old_name':old})
        for r in self.db.execute('SELECT face_id,person_id FROM face_voice_links').fetchall():
            if self.canonical_person(r['person_id'])!=person_id:continue
            face=self.canonical_face(r['face_id']);old=self.db.execute('SELECT name FROM faces WHERE id=?',(face,)).fetchone()[0]
            if old!=name:self.db.execute('UPDATE faces SET name=? WHERE id=?',(name,face));self._audit('linked_identity_named',{'face_id':face,'name':name,'old_name':old})
    def link_face_voice(self,face_id,person_id,reason):
        from .project_store import _text
        reason=_text(reason,1000)
        with self._transaction():
            face_id=self.canonical_face(face_id);person_id=self.canonical_person(person_id)
            face_name=self.db.execute('SELECT name FROM faces WHERE id=?',(face_id,)).fetchone()[0]
            voice_name=self.db.execute('SELECT name FROM people WHERE id=?',(person_id,)).fetchone()[0]
            old=self.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face_id,)).fetchone()
            if old and self.canonical_person(old[0])==person_id:return
            self.db.execute('INSERT OR REPLACE INTO face_voice_links VALUES(?,?,?,?)',(face_id,person_id,reason,time.time()))
            if bool(face_name)!=bool(voice_name):self._sync_linked_name(person_id,face_name or voice_name)
            self.db.execute('DELETE FROM av_link_feedback WHERE face_id=? AND person_id=?',(face_id,person_id))
            self._audit('face_voice_linked',{'face_id':face_id,'person_id':person_id,'previous_person_id':old[0] if old else None,'reason':reason,'source':'operator'})
    def unlink_face_voice(self,face_id,reason):
        from .project_store import _text
        reason=_text(reason,1000)
        with self._transaction():
            face_id=self.canonical_face(face_id);old=self.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face_id,)).fetchone()
            if not old:return
            self.db.execute('DELETE FROM face_voice_links WHERE face_id=?',(face_id,));self._audit('face_voice_unlinked',{'face_id':face_id,'previous_person_id':old[0],'reason':reason,'source':'operator'})
    def face_feedback(self):
        return [dict(r,details=json.loads(r['details'])) for r in self.db.execute('SELECT * FROM face_feedback ORDER BY created_at DESC')]
    def reject_voice_candidate(self,face_id,person_id):
        with self._transaction():
            f=self.canonical_face(face_id);p=self.canonical_person(person_id)
            self.db.execute("INSERT OR REPLACE INTO av_link_feedback VALUES(?,?,'rejected',?)",(f,p,time.time()))
            self._audit('face_voice_candidate_rejected',{'face_id':f,'person_id':p})
    def _record_face_feedback(self,a,b,decision,comment=''):
        from .project_store import _id,_json
        details={'comment':comment,'observations_a':[o['observation_id'] for o in self.face_observations() if o['face_id']==a],
                 'observations_b':[o['observation_id'] for o in self.face_observations() if o['face_id']==b]}
        key=_id('feedback');self.db.execute('INSERT INTO face_feedback VALUES(?,?,?,?,?,?)',(key,a,b,decision,_json(details),time.time()))
        self._audit('face_matching_feedback',{'id':key,'face_a':a,'face_b':b,'decision':decision,**details})
        return key
    def review_face_matching(self,decision,face_a=None,face_b=None,comment=''):
        from .project_store import _text
        if decision not in ('different','too_fragmented','wrong_matches','looks_right'):raise ValueError('Invalid matching feedback')
        if comment:comment=_text(comment,2000)
        with self._transaction():
            a=self.canonical_face(face_a) if face_a else None;b=self.canonical_face(face_b) if face_b else None
            if decision=='different' and (not a or not b or a==b):raise ValueError('Choose two separate face groups')
            return self._record_face_feedback(a,b,decision,comment)
    def faces_marked_different(self,a,b):
        for r in self.db.execute("SELECT face_a,face_b FROM face_feedback WHERE decision='different'"):
            if {self.canonical_face(r['face_a']),self.canonical_face(r['face_b'])}=={a,b}:return True
        return False
    def face_groups_contradict(self,source_id,target_id):
        source_id=self.canonical_face(source_id);target_id=self.canonical_face(target_id)
        return self.db.execute('''SELECT 1 FROM face_observations a JOIN face_observations b ON a.content_id=b.content_id
            WHERE a.face_id=? AND b.face_id=? AND ABS(a.seconds-b.seconds)<.02
            AND (a.frame_ref=b.frame_ref OR a.frame_ref='' OR b.frame_ref='')
            AND (a.detection_id<>b.detection_id OR a.bbox<>b.bbox) LIMIT 1''',(source_id,target_id)).fetchone() is not None
    def merge_faces(self,source_id,target_id,reason,*,chosen_name=None,feedback=False):
        from .project_store import _text
        reason=_text(reason,1000)
        with self._transaction():
            source_id=self.canonical_face(source_id);target_id=self.canonical_face(target_id)
            if source_id==target_id:return target_id
            if not feedback and self.faces_marked_different(source_id,target_id):raise ValueError('Operator marked these face groups as different people')
            if self.face_groups_contradict(source_id,target_id):raise ValueError('Resolve distinct faces appearing in the same frame before merging')
            names={r['id']:r['name'] for r in self.db.execute('SELECT id,name FROM faces WHERE id IN (?,?)',(source_id,target_id))}
            if names[source_id] and names[target_id] and names[source_id]!=names[target_id] and chosen_name is None:raise ValueError('Choose a name before merging named faces')
            links={r['face_id']:self.canonical_person(r['person_id']) for r in self.db.execute('SELECT * FROM face_voice_links WHERE face_id IN (?,?)',(source_id,target_id))}
            if len(set(links.values()))>1:raise ValueError('Resolve conflicting operator voice links before merging faces')
            name=_text(chosen_name,200) if chosen_name is not None else names[target_id] or names[source_id]
            if feedback:self._record_face_feedback(source_id,target_id,'same',reason)
            self.db.execute('UPDATE face_observations SET face_id=? WHERE face_id=?',(target_id,source_id))
            self.db.execute('UPDATE faces SET merged_into=? WHERE id=?',(target_id,source_id));self.db.execute('UPDATE faces SET name=? WHERE id=?',(name,target_id))
            if links:
                person=next(iter(links.values()));self.db.execute('INSERT OR REPLACE INTO face_voice_links VALUES(?,?,?,?)',(target_id,person,'Operator merged face groups: '+reason,time.time()))
            self.db.execute('DELETE FROM face_voice_links WHERE face_id=?',(source_id,));self.db.execute('DELETE FROM face_profiles WHERE face_id=?',(source_id,));self._refresh_face_profiles(target_id)
            self._audit('faces_merged',{'source_id':source_id,'target_id':target_id,'reason':reason,'chosen_name':name,'voice_link':links})
            return target_id
    def split_face(self,face_id,observation_ids,reason,*,new_name=None):
        from .project_store import _text
        reason=_text(reason,1000);observation_ids=list(dict.fromkeys(observation_ids))
        if not observation_ids:raise ValueError('No face observations selected')
        with self._transaction():
            face_id=self.canonical_face(face_id)
            for obs in observation_ids:
                row=self.db.execute('SELECT face_id FROM face_observations WHERE id=?',(obs,)).fetchone()
                if not row or self.canonical_face(row[0])!=face_id:raise ValueError('Observation is not assigned to that face')
            new=self._new_face(_text(new_name,200) if new_name else None)
            for obs in observation_ids:self.db.execute("UPDATE face_observations SET face_id=?,status='operator_corrected' WHERE id=?",(new,obs))
            self._refresh_face_profiles(face_id);self._refresh_face_profiles(new)
            # A split does not silently copy an operator's voice association.
            self._record_face_feedback(face_id,new,'different','Operator separated face samples: '+reason)
            self._audit('face_split',{'source_id':face_id,'new_id':new,'observation_ids':observation_ids,'reason':reason,'voice_link_copied':False})
            return new
