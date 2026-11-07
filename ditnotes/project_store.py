"""Durable project entities and operator edits; no retrieval engine or ML model.

Speaker comparisons are enrollment decisions within a model namespace. General
text/image retrieval remains in the existing Orama indexes.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib,json,math,sqlite3,time,uuid
from .atomic import fsync_dir
from pathlib import Path


def unit(vector):
    vector=[float(x) for x in vector]
    if not vector or any(not math.isfinite(x) for x in vector):raise ValueError('Invalid embedding')
    norm=math.sqrt(sum(x*x for x in vector))
    if norm<=1e-12:raise ValueError('Zero embedding')
    return [x/norm for x in vector]

def cosine(a,b):
    if len(a)!=len(b):raise ValueError('Embedding dimensions differ')
    return sum(x*y for x,y in zip(unit(a),unit(b)))

def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def _id(prefix):return prefix+'_'+uuid.uuid4().hex

def _text(value,limit=20000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError('Invalid text')
    return value.strip()


from .face_store import FaceStoreMixin

class ProjectStore(FaceStoreMixin):
    def __init__(self,db_path,project_id=None):
        from .sqlite_safety import require_safe_sqlite
        require_safe_sqlite()
        self.path=Path(db_path)
        if self.path.is_symlink():raise ValueError('Registry cannot be a symlink')
        missing=[];parent=self.path.parent
        while not parent.exists():missing.append(parent);parent=parent.parent
        self.path.parent.mkdir(parents=True,exist_ok=True)
        for directory in reversed(missing):fsync_dir(directory.parent)
        self.db=sqlite3.connect(self.path,timeout=30)
        self.db.row_factory=sqlite3.Row
        try:
            self._initialize(project_id)
            # Persist initial DB/WAL directory entries before any caller can save.
            fsync_dir(self.path.parent)
        except BaseException:
            self.db.close();fsync_dir(self.path.parent);raise
    def _initialize(self,project_id):
        # Simultaneous first opens can race while switching a new database into
        # WAL. SQLite may return BUSY here without waiting for connect(timeout).
        deadline=time.monotonic()+30
        while True:
            try:journal=self.db.execute('PRAGMA journal_mode=WAL').fetchone()[0];break
            except sqlite3.OperationalError as error:
                if getattr(error,'sqlite_errorcode',None) not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or time.monotonic()>=deadline:raise
                time.sleep(.02)
        if str(journal).lower()!='wal':raise RuntimeError('Metadata storage requires SQLite WAL mode')
        self.db.execute('PRAGMA synchronous=FULL')
        # SQLite uses F_FULLFSYNC on macOS when available, including checkpoints.
        self.db.execute('PRAGMA fullfsync=ON');self.db.execute('PRAGMA checkpoint_fullfsync=ON')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS embedding_models(revision TEXT PRIMARY KEY,dimension INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,name TEXT,merged_into TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(
          id TEXT PRIMARY KEY,content_id TEXT NOT NULL,local_speaker TEXT NOT NULL,
          start REAL NOT NULL,end REAL NOT NULL,person_id TEXT NOT NULL,
          model_revision TEXT NOT NULL,dimension INTEGER NOT NULL,embedding TEXT NOT NULL,
          quality REAL NOT NULL,quality_components TEXT NOT NULL,enrolled INTEGER NOT NULL,
          status TEXT NOT NULL,candidates TEXT NOT NULL,created_at REAL NOT NULL,
          FOREIGN KEY(person_id) REFERENCES people(id));
        CREATE INDEX IF NOT EXISTS observations_clip ON observations(content_id,start,end);
        CREATE TABLE IF NOT EXISTS clusters(content_id TEXT NOT NULL,local_speaker TEXT NOT NULL,person_id TEXT NOT NULL,
          PRIMARY KEY(content_id,local_speaker),FOREIGN KEY(person_id) REFERENCES people(id));
        CREATE TABLE IF NOT EXISTS voice_domains(content_id TEXT NOT NULL,local_speaker TEXT NOT NULL,domain TEXT NOT NULL,PRIMARY KEY(content_id,local_speaker));
        CREATE TABLE IF NOT EXISTS profiles(person_id TEXT NOT NULL,model_revision TEXT NOT NULL,dimension INTEGER NOT NULL,
          centroid TEXT NOT NULL,samples INTEGER NOT NULL,PRIMARY KEY(person_id,model_revision,dimension));
        CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY,content_id TEXT NOT NULL,start REAL,end REAL,
          text TEXT NOT NULL,metadata TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT,created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS annotations_clip ON annotations(content_id);
        CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY,from_content TEXT NOT NULL,to_content TEXT NOT NULL,
          annotation_id TEXT NOT NULL,model_revision TEXT NOT NULL,cosine REAL NOT NULL,status TEXT NOT NULL,
          inherited_annotation_id TEXT,created_at REAL NOT NULL,annotation_snapshot TEXT NOT NULL DEFAULT '{}',UNIQUE(from_content,to_content,annotation_id,model_revision));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,operation TEXT NOT NULL,
          payload TEXT NOT NULL,created_at REAL NOT NULL);
        ''')
        self._init_faces()
        if 'annotation_snapshot' not in {r[1] for r in self.db.execute('PRAGMA table_info(links)')}:
            self.db.execute("ALTER TABLE links ADD COLUMN annotation_snapshot TEXT NOT NULL DEFAULT '{}'")
        # executescript above is intentionally schema-only and may auto-commit.
        # Identity/revision initialization is a separate explicit write transaction.
        with self._transaction():
            old=self.db.execute("SELECT value FROM meta WHERE key='project_id'").fetchone()
            if old and project_id and old[0]!=project_id:raise ValueError('Registry belongs to another project')
            self.project_id=old[0] if old else project_id or _id('project')
            self.db.execute("INSERT OR IGNORE INTO voice_domains SELECT DISTINCT content_id,local_speaker,'default' FROM observations")
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('project_id',?)",(self.project_id,))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('edit_revision','0')")
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('indexed_edit_revision','-1')")
    def close(self):
        try:self.db.close()
        finally:
            # Last-connection close may checkpoint and remove WAL/SHM entries.
            fsync_dir(self.path.parent)
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
    @contextmanager
    def _transaction(self):
        if self.db.in_transaction:
            savepoint='edit_'+uuid.uuid4().hex
            self.db.execute('SAVEPOINT '+savepoint)
            try:
                yield
                self.db.execute('RELEASE SAVEPOINT '+savepoint)
            except BaseException:
                self.db.execute('ROLLBACK TO SAVEPOINT '+savepoint)
                self.db.execute('RELEASE SAVEPOINT '+savepoint)
                raise
            return
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
            # Covers creation/deletion of WAL files as well as initial DB creation.
            fsync_dir(self.path.parent)
        except BaseException:
            self.db.rollback();fsync_dir(self.path.parent);raise
    def _audit(self,operation,payload):
        self.db.execute('INSERT INTO audit(operation,payload,created_at) VALUES(?,?,?)',(operation,_json(payload),time.time()))
        self.db.execute("UPDATE meta SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='edit_revision'")
    @property
    def edit_revision(self):return int(self.db.execute("SELECT value FROM meta WHERE key='edit_revision'").fetchone()[0])
    @property
    def indexed_edit_revision(self):return int(self.db.execute("SELECT value FROM meta WHERE key='indexed_edit_revision'").fetchone()[0])
    def mark_indexed(self,revision):
        revision=int(revision)
        with self._transaction():
            if revision<0 or revision>self.edit_revision:raise ValueError('Invalid indexed edit revision')
            if revision>self.indexed_edit_revision:self.db.execute("UPDATE meta SET value=? WHERE key='indexed_edit_revision'",(str(revision),))
    def canonical_person(self,person_id):
        seen=set()
        while True:
            if person_id in seen:raise ValueError('Person merge cycle')
            seen.add(person_id);row=self.db.execute('SELECT merged_into FROM people WHERE id=?',(person_id,)).fetchone()
            if not row:raise KeyError(person_id)
            if not row[0]:return person_id
            person_id=row[0]
    def _new_person(self,name=None):
        person=_id('person');self.db.execute('INSERT INTO people VALUES(?,?,NULL,?)',(person,name,time.time()));return person
    def people(self):
        return [dict(row,identity_basis='voice') for row in self.db.execute('SELECT * FROM people ORDER BY created_at,id')]
    def _refresh_profiles(self,person_id):
        self.db.execute('DELETE FROM profiles WHERE person_id=?',(person_id,))
        groups={}
        for row in self.db.execute('SELECT model_revision,dimension,embedding FROM observations WHERE person_id=? AND enrolled=1',(person_id,)):
            groups.setdefault((row['model_revision'],row['dimension']),[]).append(json.loads(row['embedding']))
        for (revision,dimension),vectors in groups.items():
            centroid=unit([sum(v[i] for v in vectors) for i in range(dimension)])
            self.db.execute('INSERT INTO profiles VALUES(?,?,?,?,?)',(person_id,revision,dimension,_json(centroid),len(vectors)))
    def _bind_domain(self,content_id,local_speaker,domain):
        domain=_text(domain,300)
        old=self.db.execute('SELECT domain FROM voice_domains WHERE content_id=? AND local_speaker=?',(content_id,local_speaker)).fetchone()
        if old and old[0]!=domain:raise ValueError('Local speaker overlap domain changed')
        self.db.execute('INSERT OR IGNORE INTO voice_domains VALUES(?,?,?)',(content_id,local_speaker,domain))
    def assign_speaker(self,content_id,local_speaker,start,end,embedding,model_revision,quality,*,
                       quality_components=None,overlap=False,min_quality=.7,min_duration=1.5,
                       match_threshold=.82,margin=.08,auto_assign=False,exclude_person_ids=(),
                       max_enrollments_per_local_speaker=3,observation_id=None,calibration_id=None,overlap_group='default'):
        content_id=_text(content_id,128);local_speaker=_text(local_speaker,128);model_revision=_text(model_revision,1000)
        start=float(start);end=float(end);quality=float(quality)
        if not all(math.isfinite(x) for x in (start,end,quality)) or start<0 or end<=start or not 0<=quality<=1:raise ValueError('Invalid speaker interval or quality')
        if not -1<=match_threshold<=1 or not 0<=margin<=2:raise ValueError('Invalid matching policy')
        vector=unit(embedding);dimension=len(vector)
        observation_id=observation_id or 'voice_'+hashlib.sha256(_json([content_id,local_speaker,start,end,model_revision,dimension]).encode()).hexdigest()
        quality_components=quality_components or {}
        if not isinstance(quality_components,dict):raise ValueError('Quality components must be an object')
        with self._transaction():
            self._bind_domain(content_id,local_speaker,overlap_group)
            declared=self.db.execute('SELECT dimension FROM embedding_models WHERE revision=?',(model_revision,)).fetchone()
            if declared and declared[0]!=dimension:raise ValueError('Speaker model revision changed embedding dimension')
            self.db.execute('INSERT OR IGNORE INTO embedding_models VALUES(?,?)',(model_revision,dimension))
            old=self.db.execute('SELECT * FROM observations WHERE id=?',(observation_id,)).fetchone()
            if old:return self._observation(old)
            exclusions={self.canonical_person(p) for p in exclude_person_ids}
            # Distinct diarized voices sounding simultaneously cannot be one person.
            exclusions.update(self.canonical_person(r[0]) for r in self.db.execute(
                'SELECT o.person_id FROM observations o JOIN voice_domains d ON o.content_id=d.content_id AND o.local_speaker=d.local_speaker WHERE o.content_id=? AND o.local_speaker<>? AND o.start<? AND o.end>? AND d.domain=?',
                (content_id,local_speaker,end,start,overlap_group)))
            candidates=[]
            clean=not overlap and quality>=min_quality and end-start>=min_duration
            if clean:
                for row in self.db.execute('SELECT p.* FROM profiles p JOIN people e ON p.person_id=e.id WHERE p.model_revision=? AND p.dimension=? AND e.merged_into IS NULL',(model_revision,dimension)):
                    if row['person_id'] not in exclusions:
                        candidates.append({'person_id':row['person_id'],'cosine':sum(a*b for a,b in zip(vector,json.loads(row['centroid'])))})
                candidates.sort(key=lambda x:(-x['cosine'],x['person_id']))
            strong=bool(candidates and candidates[0]['cosine']>=match_threshold and
                        (len(candidates)<2 or candidates[0]['cosine']-candidates[1]['cosine']>=margin))
            cluster=self.db.execute('SELECT person_id FROM clusters WHERE content_id=? AND local_speaker=?',(content_id,local_speaker)).fetchone()
            if cluster:
                person=self.canonical_person(cluster[0]);status='local_cluster'
                if person in exclusions:raise ValueError('Local speaker conflicts with overlapping distinct voice')
            elif clean and strong and auto_assign:
                person=candidates[0]['person_id'];status='matched'
            else:
                person=self._new_person();status='provisional' if clean else 'insufficient_evidence'
                self.db.execute('INSERT INTO clusters VALUES(?,?,?)',(content_id,local_speaker,person))
            self.db.execute('INSERT OR IGNORE INTO clusters VALUES(?,?,?)',(content_id,local_speaker,person))
            count=self.db.execute('SELECT COUNT(*) FROM observations WHERE content_id=? AND local_speaker=? AND model_revision=? AND enrolled=1',(content_id,local_speaker,model_revision)).fetchone()[0]
            enrolled=bool(clean and count<max_enrollments_per_local_speaker)
            self.db.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (observation_id,content_id,local_speaker,start,end,person,model_revision,dimension,_json(vector),quality,
                 _json(quality_components),int(enrolled),status,_json(candidates[:5]),time.time()))
            # The SQL column count is checked by the smoke tests.
            if enrolled:self._refresh_profiles(person)
            self._audit('speaker_observed',{'observation_id':observation_id,'person_id':person,'status':status,'auto_assign':auto_assign,'policy':{'threshold':match_threshold,'margin':margin,'calibration_id':calibration_id},'quality_is_probability':False})
            return self._observation(self.db.execute('SELECT * FROM observations WHERE id=?',(observation_id,)).fetchone())
    def provisional_speaker(self,content_id,local_speaker,start,end,*,reason='insufficient_clean_speech',model_revision=None,quality_components=None,overlap_group='default'):
        """Preserve an identified local turn without inventing a voice vector."""
        content_id=_text(content_id,128);local_speaker=_text(local_speaker,128);start=float(start);end=float(end)
        if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:raise ValueError('Invalid speaker interval')
        reason=_text(reason,1000);revision=model_revision or 'unembedded';components=quality_components or {}
        if not isinstance(components,dict):raise ValueError('Quality components must be an object')
        observation='voice_'+hashlib.sha256(_json([content_id,local_speaker,start,end,revision,'no_embedding']).encode()).hexdigest()
        with self._transaction():
            self._bind_domain(content_id,local_speaker,overlap_group)
            old=self.db.execute('SELECT * FROM observations WHERE id=?',(observation,)).fetchone()
            if old:return self._observation(old)
            row=self.db.execute('SELECT person_id FROM clusters WHERE content_id=? AND local_speaker=?',(content_id,local_speaker)).fetchone()
            person=self.canonical_person(row[0]) if row else self._new_person()
            self.db.execute('INSERT OR IGNORE INTO clusters VALUES(?,?,?)',(content_id,local_speaker,person))
            self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(observation,content_id,local_speaker,start,end,person,revision,0,'null',0,_json(dict(components,reason=reason)),0,'insufficient_evidence','[]',time.time()))
            self._audit('speaker_provisional',{'observation_id':observation,'person_id':person,'reason':reason})
            return self._observation(self.db.execute('SELECT * FROM observations WHERE id=?',(observation,)).fetchone())
    def record_turn(self,content_id,local_speaker,start,end,model_revision,*,overlap=False,overlap_group='default'):
        """Persist turn coverage using a local cluster mapping, without a new vector."""
        existed=self.db.execute('SELECT person_id FROM clusters WHERE content_id=? AND local_speaker=?',(content_id,local_speaker)).fetchone()
        result=self.provisional_speaker(content_id,local_speaker,start,end,reason='diarized_turn',model_revision=model_revision,quality_components={'overlap':bool(overlap),'identity_basis':'local_diarization_cluster'},overlap_group=overlap_group)
        if existed and result['status']=='insufficient_evidence':
            with self._transaction():
                self.db.execute("UPDATE observations SET status='local_cluster' WHERE id=?",(result['observation_id'],))
            result['status']='local_cluster'
        return result
    def _observation(self,row):
        result=dict(row)
        for key in ('embedding','quality_components','candidates'):result[key]=json.loads(result[key])
        result['person_id']=self.canonical_person(result['person_id']);result['observation_id']=result.pop('id')
        return result
    def observations(self,content_id=None):
        rows=self.db.execute('SELECT * FROM observations'+(' WHERE content_id=?' if content_id else '')+' ORDER BY start,end,id',((content_id,) if content_id else ()))
        return [self._observation(row) for row in rows]
    def name_person(self,person_id,name):
        name=_text(name,200) if name is not None else None
        with self._transaction():
            person_id=self.canonical_person(person_id);old=self.db.execute('SELECT name FROM people WHERE id=?',(person_id,)).fetchone()[0]
            if name==old:return
            self.db.execute('UPDATE people SET name=? WHERE id=?',(name,person_id));self._audit('person_named',{'person_id':person_id,'old_name':old,'name':name})
            if name:self._sync_linked_name(person_id,name)
    def merge_people(self,source_id,target_id,reason,*,chosen_name=None):
        reason=_text(reason,1000)
        with self._transaction():
            source_id=self.canonical_person(source_id);target_id=self.canonical_person(target_id)
            if source_id==target_id:return target_id
            contradiction=self.db.execute('''SELECT 1 FROM observations a JOIN observations b ON a.content_id=b.content_id
                JOIN voice_domains da ON a.content_id=da.content_id AND a.local_speaker=da.local_speaker
                JOIN voice_domains db ON b.content_id=db.content_id AND b.local_speaker=db.local_speaker
                WHERE a.person_id=? AND b.person_id=? AND a.local_speaker<>b.local_speaker AND da.domain=db.domain
                AND MIN(a.end,b.end)>MAX(a.start,b.start)+0.000001 LIMIT 1''',(source_id,target_id)).fetchone()
            if contradiction:raise ValueError('Resolve overlapping distinct speakers in the same audio stream before merging')
            names={r['id']:r['name'] for r in self.db.execute('SELECT id,name FROM people WHERE id IN (?,?)',(source_id,target_id))}
            if names[source_id] and names[target_id] and names[source_id]!=names[target_id] and chosen_name is None:raise ValueError('Choose a name before merging named people')
            if chosen_name is not None:chosen_name=_text(chosen_name,200)
            name=chosen_name or names[target_id] or names[source_id]
            self.db.execute('UPDATE observations SET person_id=? WHERE person_id=?',(target_id,source_id))
            self.db.execute('UPDATE clusters SET person_id=? WHERE person_id=?',(target_id,source_id))
            self.db.execute('UPDATE people SET merged_into=? WHERE id=?',(target_id,source_id))
            self.db.execute('UPDATE people SET name=? WHERE id=?',(name,target_id))
            self.db.execute('DELETE FROM profiles WHERE person_id=?',(source_id,));self._refresh_profiles(target_id)
            self._audit('people_merged',{'source_id':source_id,'target_id':target_id,'reason':reason,'chosen_name':name})
            return target_id
    def reassign_observations(self,observation_ids,person_id,reason):
        observation_ids=list(dict.fromkeys(observation_ids));reason=_text(reason,1000)
        if not observation_ids:raise ValueError('No observations selected')
        with self._transaction():
            person_id=self.canonical_person(person_id);old=set();clusters=set()
            for observation in observation_ids:
                row=self.db.execute('SELECT person_id,content_id,local_speaker FROM observations WHERE id=?',(observation,)).fetchone()
                if not row:raise KeyError(observation)
                old.add(row['person_id']);clusters.add((row['content_id'],row['local_speaker']))
                self.db.execute("UPDATE observations SET person_id=?,status='operator_corrected' WHERE id=?",(person_id,observation))
            for p in old|{person_id}:self._refresh_profiles(p)
            for content,local in clusters:
                assigned={r[0] for r in self.db.execute('SELECT DISTINCT person_id FROM observations WHERE content_id=? AND local_speaker=?',(content,local))}
                if len(assigned)==1:self.db.execute('UPDATE clusters SET person_id=? WHERE content_id=? AND local_speaker=?',(next(iter(assigned)),content,local))
            self._audit('observations_reassigned',{'observation_ids':observation_ids,'person_id':person_id,'reason':reason})
    def split_person(self,person_id,observation_ids,reason,*,new_name=None):
        observation_ids=list(dict.fromkeys(observation_ids))
        if not observation_ids:raise ValueError('No observations selected')
        # Validate ownership and move observations in one writer transaction.
        with self._transaction():
            person_id=self.canonical_person(person_id)
            for obs in observation_ids:
                row=self.db.execute('SELECT person_id FROM observations WHERE id=?',(obs,)).fetchone()
                if not row or self.canonical_person(row[0])!=person_id:raise ValueError('Observation is not assigned to that person')
            new=self._new_person(_text(new_name,200) if new_name else None)
            for obs in observation_ids:self.db.execute("UPDATE observations SET person_id=?,status='operator_corrected' WHERE id=?",(new,obs))
            self._refresh_profiles(person_id);self._refresh_profiles(new)
            for row in self.db.execute('SELECT content_id,local_speaker FROM clusters WHERE person_id=?',(person_id,)):
                assigned={r[0] for r in self.db.execute('SELECT DISTINCT person_id FROM observations WHERE content_id=? AND local_speaker=?',(row[0],row[1]))}
                if assigned=={new}:self.db.execute('UPDATE clusters SET person_id=? WHERE content_id=? AND local_speaker=?',(new,row[0],row[1]))
            self._audit('person_split',{'source_id':person_id,'new_id':new,'observation_ids':observation_ids,'reason':_text(reason,1000)})
        return new
    def add_annotation(self,content_id,text,*,start=None,end=None,metadata=None):
        content_id=_text(content_id,128);text=_text(text)
        if (start is None)!=(end is None):raise ValueError('Both annotation bounds are required')
        if start is not None:
            start=float(start);end=float(end)
            if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<start:raise ValueError('Invalid annotation bounds')
        metadata=metadata or {}
        if not isinstance(metadata,dict):raise ValueError('Metadata must be an object')
        annotation=_id('annotation')
        with self._transaction():
            self.db.execute('INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?)',(annotation,content_id,start,end,text,_json(metadata),'operator',None,time.time()))
            self._audit('annotation_added',{'annotation_id':annotation,'content_id':content_id})
        return annotation
    def update_annotation(self,annotation_id,text,*,metadata=None):
        text=_text(text)
        with self._transaction():
            row=self.db.execute('SELECT * FROM annotations WHERE id=?',(annotation_id,)).fetchone()
            if not row:raise KeyError(annotation_id)
            if row['source']!='operator':raise ValueError('Inherited evidence cannot be edited as an original operator fact')
            metadata=json.loads(row['metadata']) if metadata is None else metadata
            if not isinstance(metadata,dict):raise ValueError('Metadata must be an object')
            if text==row['text'] and _json(metadata)==row['metadata']:return
            self.db.execute('UPDATE annotations SET text=?,metadata=? WHERE id=?',(text,_json(metadata),annotation_id))
            self._audit('annotation_updated',{'annotation_id':annotation_id,'old_text':row['text'],'old_metadata':json.loads(row['metadata'])})
    def annotations(self,content_id=None):
        rows=self.db.execute('SELECT * FROM annotations'+(' WHERE content_id=?' if content_id else '')+' ORDER BY created_at,id',((content_id,) if content_id else ()))
        result=[]
        for row in rows:
            item=dict(row);item['metadata']=json.loads(item['metadata']);result.append(item)
        return result
    def suggest_links(self,new_content_id,new_embedding,existing_embeddings,model_revision,*,threshold=.9,max_suggestions=3):
        """existing_embeddings contains at most16 native-Orama candidate packets.
        This method verifies similarity/provenance; it does not discover neighbors.

        A clip match only offers an annotation link. It never propagates voice IDs,
        names, facts or AI captions and never mutates the source annotation.
        """
        new_content_id=_text(new_content_id,128);model_revision=_text(model_revision,1000);new=unit(new_embedding)
        if len(existing_embeddings)>16:raise ValueError('Pass bounded native-Orama candidates, not a second corpus scan')
        if not isinstance(max_suggestions,int) or not 1<=max_suggestions<=16:raise ValueError('Invalid suggestion count')
        if not -1<=threshold<=1:raise ValueError('Invalid link threshold')
        candidates=[]
        for content,packet in existing_embeddings.items():
            if content==new_content_id:continue
            # Exact byte identity is already one content record, not an inherited link.
            if not isinstance(packet,dict) or packet.get('model_revision')!=model_revision:raise ValueError('CLIP model/profile revisions differ')
            score=cosine(new,packet['vector'])
            if score>=threshold:candidates.append((score,content))
        candidates.sort(reverse=True);made=[];created=[]
        with self._transaction():
            for score,content in candidates[:max_suggestions]:
                for row in self.db.execute("SELECT * FROM annotations WHERE content_id=? AND source='operator'",(content,)).fetchall():
                    link=_id('link')
                    snapshot={'annotation_id':row['id'],'text':row['text'],'metadata':json.loads(row['metadata']),'source_edit_revision':self.edit_revision}
                    inserted=self.db.execute('INSERT OR IGNORE INTO links(id,from_content,to_content,annotation_id,model_revision,cosine,status,created_at,annotation_snapshot) VALUES(?,?,?,?,?,?,?,?,?)',(link,content,new_content_id,row['id'],model_revision,score,'suggested',time.time(),_json(snapshot)))
                    found=self.db.execute('SELECT * FROM links WHERE from_content=? AND to_content=? AND annotation_id=? AND model_revision=?',(content,new_content_id,row['id'],model_revision)).fetchone()
                    made.append(dict(found))
                    if inserted.rowcount:created.append(found['id'])
            if created:self._audit('metadata_links_suggested',{'target_content':new_content_id,'link_ids':created,'model_revision':model_revision,'threshold':threshold})
        return made
    def links(self,content_id=None,*,status=None):
        conditions=[];values=[]
        if content_id is not None:conditions.append('to_content=?');values.append(content_id)
        if status is not None:conditions.append('status=?');values.append(status)
        rows=self.db.execute('SELECT * FROM links'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY created_at,id',values)
        return [dict(row,annotation_snapshot=json.loads(row['annotation_snapshot'])) for row in rows]
    def accept_link(self,link_id,*,automatic=False):
        with self._transaction():
            link=self.db.execute('SELECT * FROM links WHERE id=?',(link_id,)).fetchone()
            if not link:raise KeyError(link_id)
            if link['status']=='accepted':return link['inherited_annotation_id']
            if link['status']!='suggested':raise ValueError('Link is not pending')
            original=self.db.execute('SELECT * FROM annotations WHERE id=?',(link['annotation_id'],)).fetchone()
            annotation=_id('annotation')
            # Scene-time ranges do not automatically carry across edited/new clips.
            snapshot=json.loads(link['annotation_snapshot'])
            if not snapshot:snapshot={'text':original['text'],'metadata':json.loads(original['metadata']),'source_edit_revision':None}
            metadata=snapshot['metadata'];metadata={'suggested_metadata':metadata,'source_annotation_edit_revision':snapshot['source_edit_revision'],'source_content_id':link['from_content'],'source_annotation_id':original['id'],'clip_model_revision':link['model_revision'],'clip_cosine':link['cosine'],'decision_source':'automatic' if automatic else 'operator'}
            self.db.execute('INSERT INTO annotations VALUES(?,?,NULL,NULL,?,?,?,?,?)',(annotation,link['to_content'],snapshot['text'],_json(metadata),'automatic_inheritance' if automatic else 'accepted_inheritance',link_id,time.time()))
            self.db.execute("UPDATE links SET status='accepted',inherited_annotation_id=? WHERE id=?",(annotation,link_id))
            self._audit('metadata_link_inherited_automatically' if automatic else 'metadata_link_accepted',{'link_id':link_id,'annotation_id':annotation})
            return annotation
    def remove_inherited_annotation(self,annotation_id):
        with self._transaction():
            row=self.db.execute('SELECT * FROM annotations WHERE id=?',(annotation_id,)).fetchone()
            if not row or row['source'] not in ('automatic_inheritance','accepted_inheritance'):raise ValueError('Only inherited notes can be removed here')
            self.db.execute("UPDATE links SET status='rejected' WHERE id=?",(row['source_id'],))
            self.db.execute('DELETE FROM annotations WHERE id=?',(annotation_id,))
            self._audit('inherited_note_removed',dict(row))
    def reject_link(self,link_id):
        with self._transaction():
            changed=self.db.execute("UPDATE links SET status='rejected' WHERE id=? AND status='suggested'",(link_id,))
            if changed.rowcount:self._audit('metadata_link_rejected',{'link_id':link_id})
    def audit(self):
        return [dict(row,payload=json.loads(row['payload'])) for row in self.db.execute('SELECT * FROM audit ORDER BY id')]
