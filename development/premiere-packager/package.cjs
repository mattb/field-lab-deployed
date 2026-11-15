'use strict';
// Use Adobe's unmodified packager implementation. No archive builder, manifest
// mutation, native-helper stub, service enablement or plugin loading is added.
const path=require('node:path');
const fs=require('node:fs');
const pkg=require('@adobe/uxp-devtools-core/package.json');
if(pkg.version!=='1.2.0')throw Error('Unexpected Adobe SDK version');
const Package=require('@adobe/uxp-devtools-core/src/core/client/plugin/actions/PluginPackageCommand.js');
const [manifest,output]=process.argv.slice(2);
if(!manifest||!output)throw Error('Expected manifest and output directory');
const parsed=JSON.parse(fs.readFileSync(manifest,'utf8'));
if(parsed.id!=='org.fieldlab.premiere.bridge'||parsed.host?.app!=='premierepro')throw Error('Unexpected Field Lab plugin');
(async()=>{
 // package() is the same archive method used by Adobe's CLI execute(). That CLI
 // permits disconnected-host packaging. The old CLI's native helper cannot
 // boot on arm64; requiring this pure JS class does not load or alter the host.
 const result=await new Package(null,{manifest:path.resolve(manifest),packageDir:path.resolve(output),apps:[]}).package();
 if(result.length!==1||!result[0].success||result[0].host!=='premierepro')throw Error('Adobe SDK packaging failed');
 console.log(JSON.stringify({sdk:'@adobe/uxp-devtools-core',version:pkg.version,method:'PluginPackageCommand.package',result,ccx:path.join(path.resolve(output),parsed.id+'_premierepro.ccx')}));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
