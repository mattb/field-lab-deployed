"""Offline persistent-worker media analysis. Outputs contain only portable relative assets."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import time
import uuid


from .audio_context import transcript_window, contextual_prompt
from .diarization import DiarizationModels
from .scratch import scratch_directory


class AnalysisError(RuntimeError):
    def __init__(self, stage, message):
        self.stage=stage
        super().__init__(f'{stage}: {message}')


def _save(path,data):
    path=Path(path);part=path.with_suffix(path.suffix+'.tmp')
    part.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=float)+'\n');part.replace(path)


def _run(command,timeout):
    try:
        p=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as e:raise AnalysisError('extraction','media tool timed out') from e
    if p.returncode:raise AnalysisError('extraction',p.stderr[-1500:])
    return p


def _srt(seconds):
    milliseconds=round(max(0,float(seconds))*1000)
    hours,milliseconds=divmod(milliseconds,3600000);minutes,milliseconds=divmod(milliseconds,60000)
    seconds,milliseconds=divmod(milliseconds,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'


def conservative_label(text):
    text=re.sub(r'<think>.*?</think>','',text,flags=re.S).strip().strip('"')
    text=re.sub(r'\b(dead|dying|injured|healthy|happy|sad|angry|frightened)\s+','',text,flags=re.I)
    text=re.sub(r'\b(firefighter|scientist|ranger)\b','person',text,flags=re.I)
    text=re.sub(r'\b(truck|SUV|ATV|UTV)\b','vehicle',text,flags=re.I)
    return re.sub(r'\s+',' ',text).strip()


def _worker_main(kind,model_path,inbox,outbox,settings):
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_DATASETS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    started=time.perf_counter()
    try:
        if kind=='asr':
            from parakeet_mlx import from_pretrained
            model=from_pretrained(model_path)
            diarizer=DiarizationModels(settings['runtime_root']) if settings['diarization'] else None
        else:
            import mlx.core as mx
            from mlx_vlm import load,generate
            from mlx_vlm.prompt_utils import apply_chat_template
            model,processor=load(model_path);model.set_dtype(mx.float16)
        outbox.put({'kind':'ready','load_seconds':time.perf_counter()-started})
    except Exception as e:
        outbox.put({'kind':'fatal','error':f'{type(e).__name__}: {e}'});return
    while True:
        job=inbox.get()
        if job is None:return
        started=time.perf_counter()
        try:
            if job.get('action')=='prewarm':
                if kind=='asr':
                    model.transcribe(job['wav'],chunk_duration=120,overlap_duration=15)
                    if diarizer is not None:
                        diarizer.warm()
                        import soundfile as sf
                        import mlx.core as mx
                        audio,sr=sf.read(job['wav'],dtype='float32')
                        diarizer.diarizer.generate(audio,sample_rate=sr,threshold=.5,min_duration=.15,merge_gap=.10,verbose=False)
                        vector=diarizer.speaker.encode(audio,sample_rate=sr);mx.eval(vector)
                else:
                    formatted=apply_chat_template(processor,model.config,'Briefly describe what is visible.',num_images=1,enable_thinking=False)
                    generate(model,processor,formatted,image=[job['image']],max_tokens=36,temperature=0.,verbose=False)
                outbox.put({'kind':'result','id':job['id'],'data':{'discarded':True},'seconds':time.perf_counter()-started})
                continue
            if kind=='asr':
                data=[]
                for track in job['tracks']:
                    result=asdict(model.transcribe(track['wav'],chunk_duration=120,overlap_duration=15))
                    offset=track['start_seconds']
                    for sentence in result.get('sentences',[]):
                        sentence['start']+=offset;sentence['end']+=offset
                        for token in sentence.get('tokens',[]):
                            token['start']+=offset;token['end']+=offset
                    data.append(dict(result,stream_index=track['stream_index']))
                if settings['audio_context']:
                    outbox.put({'kind':'transcript','id':job['id'],'data':json.loads(json.dumps(data)),'seconds':time.perf_counter()-started,'started_at':started,'finished_at':time.perf_counter()})
                if diarizer is not None:
                    for track,result in zip(job['tracks'],data):
                        offset=track['start_seconds']
                        local_sentences=json.loads(json.dumps(result.get('sentences',[])))
                        def shift(value,amount):
                            if isinstance(value,dict):return {k:(float(v)+amount if k in ('start','end') and isinstance(v,(int,float)) else shift(v,amount)) for k,v in value.items()}
                            if isinstance(value,list):return [shift(x,amount) for x in value]
                            return value
                        local_sentences=shift(local_sentences,-offset)
                        result['diarization']=shift(diarizer.analyze(track['wav'],local_sentences),offset)
            else:
                data=[]
                for frame in job['frames']:
                    prompt=contextual_prompt(job['prompt'],frame['seconds'],frame.get('audio_context',[]))
                    formatted=apply_chat_template(processor,model.config,prompt,num_images=1,enable_thinking=False)
                    result=generate(model,processor,formatted,image=[frame['absolute_image']],max_tokens=36,temperature=0.,verbose=False)
                    raw=result.text.strip();label=conservative_label(raw)
                    data.append({'seconds':frame['seconds'],'label':label,'raw':raw,'frame':frame['frame'],
                                 'audio_context':frame.get('audio_context',[]),'needs_review':not label or len(label.split())>12 or '<think>' in label})
            outbox.put({'kind':'result','id':job['id'],'data':data,'seconds':time.perf_counter()-started,'started_at':started,'finished_at':time.perf_counter()})
        except Exception as e:
            outbox.put({'kind':'error','id':job['id'],'error':f'{type(e).__name__}: {e}'})
        finally:
            try:
                import mlx.core as mx
                mx.clear_cache()
            except Exception:pass


class _Worker:
    def __init__(self,kind,path,context,settings):
        self.kind=kind;self.inbox=context.Queue();self.outbox=context.Queue();self.load_seconds=None
        self.process=context.Process(target=_worker_main,args=(kind,str(path),self.inbox,self.outbox,settings),daemon=True)
        self.process.start()
    def submit(self,job):
        identifier=uuid.uuid4().hex;self.inbox.put(dict(job,id=identifier));return identifier
    def receive(self,identifier,deadline):
        while time.monotonic()<deadline:
            try:message=self.outbox.get(timeout=min(.1,max(.001,deadline-time.monotonic())))
            except queue.Empty:
                if not self.process.is_alive():raise AnalysisError(self.kind,'worker exited unexpectedly')
                continue
            if message['kind']=='ready':self.load_seconds=message['load_seconds'];continue
            if message['kind']=='fatal':raise AnalysisError(self.kind,message['error'])
            if message.get('id')!=identifier:continue
            if message['kind']=='error':raise AnalysisError(self.kind,message['error'])
            if message['kind']=='transcript':continue
            return message
        raise AnalysisError(self.kind,'worker timed out')
    def receive_transcript(self,identifier,deadline):
        while time.monotonic()<deadline:
            try:message=self.outbox.get(timeout=min(.1,max(.001,deadline-time.monotonic())))
            except queue.Empty:
                if not self.process.is_alive():raise AnalysisError(self.kind,'worker exited unexpectedly')
                continue
            if message['kind']=='ready':self.load_seconds=message['load_seconds'];continue
            if message['kind']=='fatal':raise AnalysisError(self.kind,message['error'])
            if message.get('id')!=identifier:continue
            if message['kind']=='error':raise AnalysisError(self.kind,message['error'])
            if message['kind']=='transcript':return message
            raise AnalysisError(self.kind,'Expected the transcript before final speaker analysis')
        raise AnalysisError(self.kind,'transcript timed out')
    def close(self):
        if self.process.is_alive():
            self.inbox.put(None);self.process.join(timeout=2)
        if self.process.is_alive():self.process.terminate();self.process.join(timeout=2)
        if self.process.is_alive():self.process.kill();self.process.join(timeout=2)
        self.inbox.close();self.outbox.close()
        self.inbox.cancel_join_thread();self.outbox.cancel_join_thread()


class Engine:
    """Serial batch interface; ASR and VLM workers run concurrently within each media job.

    Engine(models_root=...).analyze(source,fresh_output) returns result.json data.
    A failed media raises AnalysisError; the next analyze call starts healthy workers.
    Always close(), or use `with Engine(...) as engine:`. Source media is read-only.
    """
    def __init__(self,models_root=None,*,timeout=300,interval=2,max_frames=12,vision_width=960,audio_context=True,diarization=True,runtime_root=None):
        root=Path(__file__).resolve().parent
        self.models_root=Path(models_root or root.parent/'models').resolve()
        self.settings={'audio_context':bool(audio_context),'diarization':bool(diarization),'runtime_root':str(Path(runtime_root or root.parent).resolve())}
        self.paths={'asr':self.models_root/'parakeet','vlm':self.models_root/'Qwen3.5-4B-4bit'}
        self.timeout=float(timeout);self.interval=float(interval);self.max_frames=int(max_frames);self.vision_width=int(vision_width)
        if self.timeout<=0 or self.interval<=0 or self.max_frames<1:raise ValueError('Positive timeout, interval and max_frames required')
        self.native=root/'native'/'feature_select';self.prompt_path=root/'prompts'/'field-caption.txt'
        self.context=mp.get_context('spawn');self.workers={};self.closed=False
        from .faces import FaceModels
        self.face_models=FaceModels();self.progress_callback=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):
        for worker in self.workers.values():worker.close()
        self.workers.clear();self.closed=True
    def _ensure_workers(self):
        if self.closed:raise AnalysisError('engine','engine is closed')
        for kind,path in self.paths.items():
            if not (path/'config.json').is_file():raise AnalysisError('models',f'Offline {kind} model is missing; run prepare-offline')
            if kind not in self.workers or not self.workers[kind].process.is_alive():
                if kind in self.workers:self.workers[kind].close()
                self.workers[kind]=_Worker(kind,path,self.context,self.settings)
    def prewarm(self,wav=None,image=None):
        """Discarded representative forwards on the same workers used by analyze."""
        if wav is None or image is None:
            import wave
            import numpy as np
            from PIL import Image
            with tempfile.TemporaryDirectory(prefix='indexer-warmup-') as directory:
                directory=Path(directory);wav=directory/'audio.wav';image=directory/'frame.jpg'
                t=np.arange(48000)/16000;pcm=(.07*np.sin(2*np.pi*220*t)*32767).astype('<i2')
                with wave.open(str(wav),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(pcm.tobytes())
                Image.new('RGB',(960,540),(128,128,128)).save(image)
                return self.prewarm(wav,image)
        started=time.perf_counter();self._ensure_workers()
        jobs={kind:worker.submit({'action':'prewarm','wav':str(wav),'image':str(image)}) for kind,worker in self.workers.items()}
        deadline=time.monotonic()+self.timeout
        stages={kind:self.workers[kind].receive(identifier,deadline)['seconds'] for kind,identifier in jobs.items()}
        faces=self.face_models.prewarm()
        return {'seconds':time.perf_counter()-started,'worker_forward_seconds':stages,'faces':faces,'discarded':True}
    def _remaining(self):
        remaining=min(self.timeout,getattr(self,'_deadline',time.monotonic()+self.timeout)-time.monotonic())
        if remaining<=0:raise AnalysisError('extraction','Media job timed out')
        return remaining
    def _audio(self,source,probe,work):
        tracks=[];origin=float(probe['format'].get('start_time',0) or 0)
        for stream in probe['streams']:
            if stream['codec_type']!='audio':continue
            index=stream['index'];wav=work/f'audio-{index}.wav'
            _run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source),'-map',f'0:{index}',
                  '-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],self._remaining())
            tracks.append({'stream_index':index,'wav':str(wav),'start_seconds':float(stream.get('start_time',origin) or origin)-origin,
                           'channels':stream.get('channels'),'channel_layout':stream.get('channel_layout'),
                           'sample_rate':stream.get('sample_rate'),'codec':stream.get('codec_name'),
                           'tags':stream.get('tags',{}),'analysis_channel_policy':'all channels in this stream downmixed to mono; original unchanged'})
        return tracks
    def _frame(self,source,time_s,index,width,directory,origin,video_index=0):
        image=directory/f'{index:04}.jpg'
        p=_run(['ffmpeg','-hide_banner','-y','-threads','1','-skip_frame','nokey','-copyts','-ss',str(time_s),
                '-noaccurate_seek','-i',str(source),'-map',f'0:{video_index}','-an','-frames:v','1','-vf',f'scale={width}:-2,showinfo',
                '-q:v','3',str(image)],self._remaining())
        pts=re.findall(r'pts_time:([-\d.]+)',p.stderr)
        if not pts or not image.is_file():raise AnalysisError('extraction','No decoded keyframe at requested offset')
        return {'seconds':float(pts[0])-origin,'source_pts':float(pts[0]),'requested_seconds':time_s,'image':str(image)}
    def _video(self,source,probe,work,output):
        from PIL import Image
        video=next((s for s in probe['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
        if not video:return []
        duration=float(video.get('duration') or probe['format']['duration']);origin=float(probe['format'].get('start_time',0) or 0)
        candidates_dir=work/'candidates';candidates_dir.mkdir()
        offsets=[i*self.interval for i in range(max(1,math.ceil(duration/self.interval)))]
        with ThreadPoolExecutor(max_workers=5) as pool:
            frames=list(pool.map(lambda item:self._frame(source,item[1],item[0],320,candidates_dir,origin,video['index']),enumerate(offsets)))
        nonblack=[];seen=set();seen_times=set()
        for frame in frames:
            with Image.open(frame['image']) as image:
                small=image.convert('RGB').resize((16,16));raw=small.tobytes()
            if max(raw)<12 or frame['seconds'] in seen_times:continue
            key=hashlib.sha256(raw).hexdigest()
            if key in seen:continue
            seen.add(key);seen_times.add(frame['seconds']);nonblack.append(frame)
        if not nonblack:return []
        if len(nonblack)==1:selected=nonblack
        else:
            if not self.native.is_file():raise AnalysisError('selection','Native selector is missing; run prepare-offline')
            _save(work/'candidates.json',{'frames':nonblack,'duration':duration})
            _run([str(self.native),str(work/'candidates.json'),str(work/'selection.json'),str(self.max_frames)],self._remaining())
            selection=json.loads((work/'selection.json').read_text());unique=[]
            for i in selection['selected_indices']:
                if all(selection['distances'][i][j]>.25 for j in unique):unique.append(i)
            selected=[selection['frames'][i] for i in unique]
        frames_dir=output/'frames';frames_dir.mkdir(exist_ok=True)
        def refine(item):
            i,frame=item
            high=self._frame(source,frame['requested_seconds'],i,self.vision_width,frames_dir,origin,video['index'])
            if abs(high['seconds']-frame['seconds'])>.002:raise AnalysisError('extraction','Selected full-resolution timestamp differs')
            return {'seconds':high['seconds'],'source_pts':high['source_pts'],'frame':str(Path(high['image']).relative_to(output)),
                    'absolute_image':high['image']}
        with ThreadPoolExecutor(max_workers=4) as pool:return list(pool.map(refine,enumerate(selected)))
    def _report(self,stage):
        if self.progress_callback:self.progress_callback(stage)
    def analyze(self,source,output):
        source=Path(source).resolve();output=Path(output).resolve();started=time.perf_counter();deadline=time.monotonic()+self.timeout
        if not source.is_file():raise AnalysisError('input','Source file does not exist')
        if output==source.parent or output in source.parents:raise AnalysisError('output','Output must be separate from source directory')
        if output.exists() and any(output.iterdir()):raise AnalysisError('output','Output directory must be empty')
        output.mkdir(parents=True,exist_ok=True)
        self._deadline=deadline
        self._report('extraction')
        probe=json.loads(_run(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',str(source)],self.timeout).stdout)
        if not any(s['codec_type'] in ('audio','video') for s in probe['streams']):raise AnalysisError('input','No supported audio or video streams')
        self._ensure_workers()
        timings={};audio_tracks=[];frames=[];jobs={}
        try:
            with scratch_directory(prefix='.work-',dir=output) as temporary:
                work=Path(temporary)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures={pool.submit(self._audio,source,probe,work):'asr',pool.submit(self._video,source,probe,work,output):'vlm'}
                    while futures:
                        done,_=wait(futures,timeout=max(.001,deadline-time.monotonic()),return_when=FIRST_COMPLETED)
                        if not done:raise AnalysisError('extraction','Extraction timed out')
                        for future in done:
                            kind=futures.pop(future);prepared=future.result();timings[f'{kind}_ready_seconds']=time.perf_counter()-started
                            if kind=='asr':
                                audio_tracks=prepared
                                if prepared:
                                    jobs[kind]=self.workers[kind].submit({'tracks':prepared});self._report('asr_and_extraction')
                            else:frames=prepared
                if frames:
                    if not self.prompt_path.is_file():raise AnalysisError('prompt','Field caption prompt is missing')
                    if self.settings['audio_context'] and 'asr' in jobs:
                        self._report('asr')
                        transcript=self.workers['asr'].receive_transcript(jobs['asr'],deadline)
                        timings['audio_context_ready_seconds']=time.perf_counter()-started
                        timings['asr_transcript_seconds']=transcript['seconds']
                        for frame in frames:frame['audio_context']=transcript_window(transcript['data'],frame['seconds'])
                    jobs['vlm']=self.workers['vlm'].submit({'frames':frames,'prompt':self.prompt_path.read_text()});self._report('analysis' if 'asr' in jobs else 'vlm')
                completed={}
                for kind,identifier in jobs.items():
                    response=self.workers[kind].receive(identifier,deadline);completed[kind]=response['data']
                    timings[f'{kind}_started_seconds']=response['started_at']-started;timings[f'{kind}_finished_seconds']=response['finished_at']-started
                    timings[f'{kind}_inference_seconds']=response['seconds'];timings[f'{kind}_model_load_seconds']=self.workers[kind].load_seconds
                    if kind=='asr' and 'vlm' in jobs:self._report('vlm')
                tracks=[];tracks_dir=output/'tracks'
                if audio_tracks:tracks_dir.mkdir()
                for metadata,data in zip(audio_tracks,completed.get('asr',[])):
                    metadata={k:v for k,v in metadata.items() if k!='wav'};stem=f'stream-{metadata["stream_index"]}'
                    track=dict(metadata,**{k:v for k,v in data.items() if k!='stream_index'},
                               json=f'tracks/{stem}.json',text_file=f'tracks/{stem}.txt',srt=f'tracks/{stem}.srt')
                    _save(tracks_dir/f'{stem}.json',track)
                    (tracks_dir/f'{stem}.txt').write_text(track.get('text','')+'\n')
                    (tracks_dir/f'{stem}.srt').write_text('\n\n'.join(f'{i}\n{_srt(s["start"])} --> {_srt(s["end"])}\n{s["text"].strip()}' for i,s in enumerate(track.get('sentences',[]),1))+'\n')
                    tracks.append(track)
                highlights=completed.get('vlm',[])
            portable_probe=json.loads(json.dumps(probe));portable_probe.get('format',{}).pop('filename',None)
            result={'schema_version':1,'status':'complete','duration':float(probe['format'].get('duration',0)),
                    'timestamp_basis':'seconds relative to media start; sampled decoded source frames',
                    'probe':portable_probe,'tracks':tracks,'highlights':highlights,
                    'models':{'asr':'parakeet-tdt-0.6b-v3','visual':'Qwen3.5-4B-4bit-fp16'},
                    'timing':dict(timings,total_seconds=time.perf_counter()-started),
                    'features':self.settings,'visual_kind':'sampled_highlights','machine_generated':True}
            self._report('faces')
            result['faces']=self.face_models.analyze(highlights,output)
            result['timing']['face_seconds']=result['faces']['timing']['total_seconds']
            result['timing']['total_seconds']=time.perf_counter()-started
            _save(output/'result.json',result);return result
        except Exception as e:
            # Quarantine all outstanding jobs: next file gets clean workers, never stale replies.
            for worker in self.workers.values():worker.close()
            self.workers.clear()
            message=str(e)
            for path in (source,output,self.models_root):message=message.replace(str(path),'<local path>')
            raise AnalysisError(getattr(e,'stage','analysis'),message) from e
