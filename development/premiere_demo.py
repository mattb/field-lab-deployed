#!/usr/bin/env python3
"""Build a new disposable, model-free editorial fixture. Never overwrite a project."""
from __future__ import annotations
import argparse,hashlib,json,math,shutil,subprocess,sys,tempfile,uuid,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from ditnotes.project_store import ProjectStore
from ditnotes.search_projection import project_documents
from ditnotes.atomic import atomic_json

SCENES=[
 {'key':'interview','path':'20261112_D01/CAM_A/A001/Morgan interview.mp4','title':'MORGAN - INTERVIEW','line':'Sea ice tells a changing story.','tags':['Interview','Sea ice'],'person':'Morgan','note':'Keep the explanation about sea ice.','range':[1,4],'color':'#163e5b','outline':'Morgan explains the changing sea ice.'},
 {'key':'field','path':'20261112_D01/CAM_B/B001/Casey sea ice field.mp4','title':'CASEY - SEA ICE FIELD','line':'We measure the ice every morning.','tags':['Sea ice'],'person':'Casey','note':'Use as a fieldwork cutaway.','range':[2,5],'color':'#235c68','outline':'Casey measures sea ice in the field.'},
 {'key':'wildlife','path':'20261112_D01/CAM_B/B001/Penguin colony.mp4','title':'PENGUIN COLONY - WILDLIFE','line':'Penguins return to this colony each year.','tags':['Wildlife'],'person':None,'note':'Wide wildlife cutaway. Keep natural sound.','range':[1,5],'color':'#374f38','outline':'A synthetic penguin-colony graphic.'},
 {'key':'product','path':'20261112_D01/CAM_A/A002/Sea ice cream.mp4','title':'SEA ICE CREAM - PRODUCT','line':'This sea ice cream is a dessert.','tags':['Sea ice cream'],'person':None,'note':'Product insert, not sea ice field footage.','range':[1,4],'color':'#64435e','outline':'A synthetic dessert product graphic.'}
]

def run(args):
 subprocess.run([str(a) for a in args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)

def graphic(path,scene):
 from PIL import Image,ImageDraw,ImageFont
 im=Image.new('RGB',(960,540),scene['color']);d=ImageDraw.Draw(im)
 fontpath='/System/Library/Fonts/Helvetica.ttc'
 font=ImageFont.truetype(fontpath,36);small=ImageFont.truetype(fontpath,24)
 d.text((48,32),scene['title'],font=font,fill='white')
 d.text((48,91),'SYNTHETIC FIELD LAB TEST MEDIA',font=small,fill='#dddddd')
 if scene['person']:
  # Deliberately schematic portrait, not a face-recognition benchmark.
  d.ellipse((398,155,562,319),fill='#ded8cb');d.rounded_rectangle((339,322,621,516),40,fill='#bbbbbb')
 else:
  for x in [300,425,550]:
   d.ellipse((x,193,x+100,430),fill='#dddddd');d.ellipse((x+23,225,x+78,384),fill='#222222')
 d.text((48,470),' / '.join(scene['tags']),font=small,fill='white');im.save(path)

def authored_audio(work,scene,ffmpeg,say):
 """Voice each word separately; record actual sample bounds, not forced alignment."""
 words=[];chunks=[];rate=24000;cursor=int(rate*.5)
 chunks.append(bytes(cursor*2))
 for index,word in enumerate(scene['line'].split()):
  wav=work/f'word-{index}.wav'
  if say:
   aiff=work/f'word-{index}.aiff';run([say,'-o',aiff,'--',word]);run([ffmpeg,'-y','-i',aiff,'-ar',rate,'-ac',1,'-c:a','pcm_s16le',wav])
   with wave.open(str(wav),'rb') as stream:frames=stream.readframes(stream.getnframes())
  else:
   # Explicitly silent fixtures still exercise transcript API; not ASR accuracy.
   frames=bytes(int(rate*.3)*2)
  count=len(frames)//2;start=cursor/rate;duration=count/rate
  words.append({'text':word,'start':round(start,6),'end':round(start+duration,6),'duration':round(duration,6),'confidence':1.0,'provenance':'authored_separate_word_audio' if say else 'authored_silent_test'})
  chunks.append(frames);chunks.append(bytes(int(rate*.10)*2));cursor+=count+int(rate*.10)
 duration=math.ceil(max(8,cursor/rate+.5)*24)/24
 chunks.append(bytes(max(0,int(duration*rate)-cursor)*2));audio=work/'audio.wav'
 with wave.open(str(audio),'wb') as stream:stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(rate);stream.writeframes(b''.join(chunks))
 return audio,duration,words

def build(destination,*,ffmpeg=None,speech=True):
 project=Path(destination).expanduser().resolve();ffmpeg=ffmpeg or shutil.which('ffmpeg')
 if not ffmpeg:raise RuntimeError('ffmpeg is required; supply --ffmpeg')
 if project.exists():raise FileExistsError('Destination must be new; existing projects are never replaced')
 project.mkdir(parents=True);notes=project/'_DIT_NOTES';notes.mkdir();project_id='synthetic-'+uuid.uuid4().hex
 identity={'id':project_id,'name':'Field Lab synthetic editorial test','schema_version':1,'synthetic':True}
 assets=[];expected={'project_id':project_id,'synthetic':True,'assets':{},'bins':{},'checks':[],'original_sha256':{}}
 try:
  with ProjectStore(notes/'project.sqlite3',project_id=project_id) as store:
   for scene in SCENES:
    work=notes/'synthetic'/scene['key'];work.mkdir(parents=True);image=work/'poster.jpg';graphic(image,scene)
    audio,duration,words=authored_audio(work,scene,ffmpeg,shutil.which('say') if speech else None)
    media=project/scene['path'];media.parent.mkdir(parents=True,exist_ok=True)
    run([ffmpeg,'-y','-loop','1','-framerate','24','-i',image,'-i',audio,'-t',str(duration),'-c:v','libx264','-preset','ultrafast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',media])
    sha=hashlib.sha256(media.read_bytes()).hexdigest();asset_id='synthetic-'+scene['key'];poster='/notes/synthetic/'+scene['key']+'/poster.jpg'
    transcript=[{'start':words[0]['start'],'end':words[-1]['end'],'text':scene['line'],'stream':1}]
    speaker_uuid=str(uuid.uuid4())
    transcript_json={'language':'en-us','speakers':[{'id':speaker_uuid,'name':scene['person'] or 'Narrator'}],'segments':[{'start':words[0]['start'],'duration':round(words[-1]['end']-words[0]['start'],6),'language':'en-us','speaker':speaker_uuid,'words':[{'text':w['text'],'start':w['start'],'duration':w['duration'],'confidence':1.0,'eos':i==len(words)-1,'tags':[],'type':'word'} for i,w in enumerate(words)]}]}
    atomic_json(work/'premiere-transcript.json',transcript_json)
    result={'schema_version':1,'duration':duration,'synthetic':True,'tracks':[{'stream_index':1,'sentences':transcript,'words':words,'speakers':[{'id':'speaker0','name':scene['person'] or 'Narrator'}]}]}
    atomic_json(work/'result.json',result)
    asset={'id':asset_id,'content_id':sha,'name':media.name,'relative_path':scene['path'],'kind':'video','duration':duration,'width':960,'height':540,'media_url':'/media/'+scene['path'],'original_url':'/media/'+scene['path'],'poster_url':poster,'status':'ready','tracks':[],'metadata':{'synthetic':True,'transcript_provenance':'Authored word audio, not ASR','camera':scene['path'].split('/')[1]},'highlights':[{'seconds':1,'note':scene['outline'],'image_url':poster,'source':'synthetic_authored'}],'transcript':transcript,'result_url':'/notes/synthetic/'+scene['key']+'/result.json','synthetic_transcript':{'words':words,'speaker':scene['person'] or 'Narrator','provenance':'authored_test'}}
    store.set_asset_tags(sha,scene['tags'],0)
    whole=store.add_annotation(sha,scene['note'],metadata={'fixture':'synthetic','purpose':'whole_asset'})
    ranged=store.add_annotation(sha,'Editorial range: '+scene['note'],start=scene['range'][0],end=scene['range'][1],metadata={'fixture':'synthetic'})
    person_id=face_id=None
    if scene['person']:
     person_id=store.provisional_speaker(sha,'speaker0',words[0]['start'],words[-1]['end'],reason='synthetic_authored_identity')['person_id'];store.name_person(person_id,scene['person'])
     face=store.assign_face(sha,1,None,'synthetic-unembedded',0,bbox=[.414,.287,.586,.591],frame_ref=poster,quality_components={'synthetic':True});face_id=face['face_id'];store.name_face(face_id,scene['person']);store.link_face_voice(face_id,person_id,'Synthetic authored fixture; not model evidence')
    asset['transcript_json']=transcript_json
    asset['outline']=[{'id':'outline-'+scene['key'],'start':0,'end':duration,'text':scene['outline']}]
    assets.append(asset);expected['assets'][scene['key']]={'id':asset_id,'content_id':sha,'path':scene['path'],'duration':duration,'tags':scene['tags'],'line':scene['line'],'word_count':len(words),'note':scene['note'],'range':scene['range'],'annotation_ids':[whole,ranged],'person':scene['person'],'person_id':person_id,'face_id':face_id}
    expected['original_sha256'][scene['path']]=sha
    for tag in scene['tags']:expected['bins'].setdefault(tag,[]).append(media.name)
   catalog={'schema_version':1,'project':identity,'assets':assets};catalog['search_projection']=project_documents(catalog,store)
  atomic_json(notes/'project.json',identity);atomic_json(notes/'catalog.json',catalog)
  expected['checks']=[
   'Four source clips import; each opens and plays with local audio.',
   'Sea ice bin contains Morgan interview and Casey sea ice field only; Sea ice cream is excluded.',
   'Interview bin contains Morgan interview only; Wildlife contains Penguin colony only.',
   'Native transcript on Morgan interview reads Sea ice tells a changing story. and names Morgan.',
   'Each clip has one authored visual outline and its human ranged note at the expected times.',
   'An editor-owned bin and marker survive a second identical synchronization; no duplicate source clips or markers.',
   'Remove Sea ice from Casey and add Needs review: membership moves after sync without deleting media.',
   'Edit a managed note in Premiere, then edit it in Field Lab: both versions survive as a visible conflict.',
   'Close Premiere or interrupt before acknowledgement, restart and synchronize: queued work recovers without duplicates.',
   'Save, close and reopen the native project: tags, metadata, markers and transcript remain; original hashes unchanged.'
  ]
  atomic_json(project/'EXPECTED-RESULTS.json',expected)
  (project/'SYNTHETIC-TEST-ONLY.txt').write_text('Disposable authored corpus. Identities are fictional labels, graphics are not face recognition evidence. Word timings are generated audio bounds, not model alignment. No user media or metadata included.\n')
  return {'project':str(project),'catalog':str(notes/'catalog.json'),'expected':str(project/'EXPECTED-RESULTS.json'),'duration_seconds':sum(a['duration'] for a in assets),'speech':bool(speech and shutil.which('say'))}
 except BaseException:
  # Preserve incomplete diagnostic material; never recursively delete an input.
  (project/'BUILD-INCOMPLETE.txt').write_text('Synthetic build interrupted; choose a new destination for retry.\n');raise

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('destination',type=Path);p.add_argument('--ffmpeg');p.add_argument('--silent',action='store_true',help='Use explicit silent authored transcript fixtures instead of macOS say');a=p.parse_args();print(json.dumps(build(a.destination,ffmpeg=a.ffmpeg,speech=not a.silent),indent=2))
if __name__=='__main__':main()
