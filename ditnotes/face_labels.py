"""Apply explicit operator assertions about the sole visible person in each clip.

No filename guessing, inference, voice assignment, or project process lock. The
shared SQLite edit transaction publishes the complete change, or none of it.
"""
from collections import defaultdict
from .project_store import _text


def label_clip_faces(store, expected_names, *, reason, override_existing_names=False):
    """Label/merge observed faces using an operator-supplied content-ID map.

    Unmapped observations retain their groups and labels. Mixed groups are split
    first. Existing different-person feedback, simultaneous-face contradictions,
    and conflicting voice associations remain hard guards even with name override.
    The caller is asserting one visible person per mapped clip, not one speaker.
    """
    if not isinstance(override_existing_names,bool):raise ValueError('Name override must be an explicit boolean')
    reason=_text(reason,1000)
    expected_names={_text(content,200):_text(name,200) for content,name in expected_names.items()}
    output={'identities':{},'splits':0,'merges':0,'observations':0}
    with store._transaction():
        observations=store.face_observations();by_group=defaultdict(list)
        faces={face['id']:face for face in store.faces()}
        for observation in observations:by_group[observation['face_id']].append(observation)
        missing=set(expected_names)-{o['content_id'] for o in observations}
        if missing:raise ValueError('No face observations for supplied clip content IDs: '+', '.join(sorted(missing)))
        # Validate existing operator labels before changing any group.
        for face_id,rows in by_group.items():
            names={expected_names[o['content_id']] for o in rows if o['content_id'] in expected_names}
            old=faces[face_id]['name']
            if old and any(name!=old for name in names) and not override_existing_names:
                raise ValueError('Conflicting existing operator face name; explicit override required')
            link=store.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face_id,)).fetchone()
            if link:
                person=store.canonical_person(link[0]);voice_name=store.db.execute('SELECT name FROM people WHERE id=?',(person,)).fetchone()[0]
                if voice_name and any(name!=voice_name for name in names):
                    raise ValueError('Resolve conflicting operator voice name before labeling faces')
        # Splitting must not erase the provenance of existing operator guards.
        # Check the original groups before a child loses its parent's feedback
        # or (intentionally uncopied) voice association.
        original_groups=defaultdict(list)
        for face_id,rows in by_group.items():
            for name in {expected_names[o['content_id']] for o in rows if o['content_id'] in expected_names}:
                original_groups[name].append(face_id)
        for groups in original_groups.values():
            linked_voices=set()
            for index,face_id in enumerate(groups):
                link=store.db.execute('SELECT person_id FROM face_voice_links WHERE face_id=?',(face_id,)).fetchone()
                if link:linked_voices.add(store.canonical_person(link[0]))
                for other in groups[:index]:
                    if store.faces_marked_different(face_id,other):
                        raise ValueError('Operator marked these face groups as different people')
            if len(linked_voices)>1:raise ValueError('Resolve conflicting operator voice links before merging faces')
        selected=defaultdict(list)
        for face_id,rows in by_group.items():
            partitions=defaultdict(list)
            for observation in rows:partitions[expected_names.get(observation['content_id'])].append(observation['observation_id'])
            mapped=sorted(name for name in partitions if name is not None)
            # Preserve an existing named anchor's canonical ID. Unmapped rows
            # stay in that original group as before; this adds no new assertion
            # about them. Otherwise preserve the original unmapped partition.
            old_name=faces[face_id]['name']
            retained=old_name if old_name in mapped else (None if None in partitions else (mapped[0] if mapped else None))
            for name in mapped:
                group=face_id if name==retained else store.split_face(face_id,partitions[name],reason)
                if group!=face_id:output['splits']+=1
                if store.face_groups_contradict(group,group):
                    raise ValueError('Resolve distinct faces appearing in the same frame before labeling')
                selected[name].append(group);output['observations']+=len(partitions[name])
        for name,groups in sorted(selected.items()):
            # Prefer an existing named anchor over an anonymous fragment even
            # when that fragment was encountered earlier in the clip timeline.
            target=next((group for group in groups if faces.get(group,{}).get('name')==name),groups[0])
            for group in groups:
                if group==target:continue
                target=store.merge_faces(group,target,reason,chosen_name=name)
                output['merges']+=1
            # Filenames/clip assertions identify faces; they do not name voices.
            store.name_face(target,name,sync_linked=False)
            output['identities'][name]=target
        if selected:store._audit('clip_faces_labeled',{'content_names':expected_names,'reason':reason,'override_existing_names':bool(override_existing_names),'voice_names_assigned':False,**output})
    return output
