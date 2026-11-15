'use strict';
// js-sha256's documented browser flag keeps UXP off Node crypto shims.
if(typeof window==='object')window.JS_SHA256_NO_NODE_JS=true;
const sha256=require('./vendor/sha256.js');
function stable(value){if(Array.isArray(value))return '['+value.map(stable).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+stable(value[k])).join(',')+'}';return JSON.stringify(value);}
function fingerprint(value){return sha256(stable(value));}
function transcriptFingerprint(value){if(typeof value==='string'){try{value=JSON.parse(value);}catch{}}return fingerprint(value);}
module.exports={stable,fingerprint,transcriptFingerprint};
