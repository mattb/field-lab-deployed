import test from 'node:test';
import assert from 'node:assert/strict';
import {activeTag,completeTag,formatTags,parseTags,sameTags,savedDraft,tagSuggestions,tagDraftKey,writeTagDraft,readTagDraft,restoreTagDraft,hasActiveTagText} from '../src/asset-tags.js';

test('comma tags trim, normalize whitespace and deduplicate without changing display spelling',()=>{
 assert.deepEqual(parseTags(' Interview,  sea   ice, interview, , ＩＮＴＥＲＶＩＥＷ, B-roll, '),['Interview','sea ice','B-roll']);
 assert.equal(formatTags([{id:'one',name:'Interview'},{id:'two',name:'Sea ice'}]),'Interview, Sea ice');
 assert.equal(sameTags(['interview','Sea ice'],[{name:'Sea ice'},{name:'Interview'}]),true);
 assert.equal(sameTags(['Interview'],[{name:'Interview'},{name:'Sea ice'}]),false);
});
test('autocomplete follows cursor token rather than blindly using the last tag',()=>{
 assert.deepEqual(activeTag('Ariel, for, night',9),{start:6,end:10,text:'for'});
 assert.deepEqual(activeTag(', forest',0),{start:0,end:0,text:''});
 const vocabulary=[{name:'Forest'},{name:'Ariel'},{name:'night'},{name:'forest'},{name:'Forest canopy'},{name:'Interview'}];
 assert.deepEqual(tagSuggestions('Ariel, for, night',9,vocabulary),['Forest','Forest canopy']);
 assert.deepEqual(tagSuggestions('Ariel, ',7,vocabulary),['Forest','Forest canopy','Interview','night']);
 assert.deepEqual(tagSuggestions('FOREST',6,vocabulary),['Forest canopy']);
});
test('completion replaces just the active token and keeps neighboring tags',()=>{
 assert.deepEqual(completeTag('Ariel, for, night',9,'Forest'),{value:'Ariel, Forest, night',cursor:15});
 assert.deepEqual(completeTag('for',3,'Forest'),{value:'Forest, ',cursor:8});
 assert.deepEqual(completeTag('Ariel, ',7,'Forest'),{value:'Ariel, Forest, ',cursor:15});
});
test('a save response cannot wipe tags entered while that request was in flight',()=>{
 assert.equal(savedDraft('Forest, interview','Forest',[{name:'Forest'}]),'Forest, interview');
 assert.equal(savedDraft('forest','forest',[{name:'Forest'}]),'Forest');
});
test('unsaved drafts survive switching assets and failed storage without crossing projects',()=>{
 const key=tagDraftKey('project-one','same-asset'),other=tagDraftKey('project-two','same-asset');
 const failing={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');},removeItem(){throw Error('blocked');}};
 writeTagDraft(key,{value:'Interview, forest',revision:3},failing);
 assert.deepEqual(readTagDraft(key,failing),{value:'Interview, forest',revision:3});
 assert.equal(readTagDraft(other,failing),null);
 assert.deepEqual(restoreTagDraft(readTagDraft(key,failing),[{name:'Interview'}],3),{value:'Interview, forest',conflict:false,keep:true});
 assert.equal(restoreTagDraft(readTagDraft(key,failing),[{name:'Other editor tag'}],4).conflict,true);
 writeTagDraft(key,null,failing);assert.equal(readTagDraft(key,failing),null);
});
test('a lost save acknowledgement resolves from server values without resurrecting deleted tags',()=>{
 assert.deepEqual(restoreTagDraft({value:'Forest',revision:1},[{name:'Forest'}],2),{value:'Forest',conflict:false,keep:false});
 assert.deepEqual(restoreTagDraft({value:'',revision:1},[],2),{value:'',conflict:false,keep:false});
 assert.deepEqual(restoreTagDraft({value:'',revision:1},[{name:'Interview'}],1),{value:'',conflict:false,keep:true});
});

test('empty and comma-completed tokens do not implicitly accept a suggestion on Tab or clearing Enter',()=>{
 assert.equal(hasActiveTagText('',0),false);
 assert.equal(hasActiveTagText('Forest, ',8),false);
 assert.equal(hasActiveTagText('   ',3),false);
 assert.equal(hasActiveTagText('Forest, int',11),true);
});

test('unknown project identity never shares a cross-project draft slot',()=>{
 writeTagDraft(null,{value:'private draft',revision:0});
 assert.equal(readTagDraft(null),null);
});
