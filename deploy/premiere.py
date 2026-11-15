#!/usr/bin/env python3
"""Seal Premiere plugin candidates and install Adobe-produced CCX packages locally.

Adobe UDT creates CCX; this tool never disguises a handmade ZIP as an installer.
UPIA owns native registration. Our atomic ledger records, rather than guesses,
unknown outcomes across crashes. Projects and Adobe preferences are never edited.
"""
from __future__ import annotations
import argparse, contextlib, fcntl, hashlib, json, os, re, shutil, sqlite3, stat, subprocess, sys, time, uuid, zipfile
from pathlib import Path, PurePosixPath
sys.path.insert(0,str(Path(__file__).resolve().parent))
import manager

PLUGIN_ID='org.fieldlab.premiere.bridge'
SOURCE=Path(__file__).resolve().parents[1]/'premiere-plugin'
UPIA=Path('/Library/Application Support/Adobe/Adobe Desktop Common/RemoteComponents/UPI/UnifiedPluginInstallerAgent/UnifiedPluginInstallerAgent.app/Contents/MacOS/UnifiedPluginInstallerAgent')
FILES=('LICENSE','manifest.json','index.html','main.js','native.js','commands.js','native-values.js','vendor/sha256.js','vendor/js-sha256-LICENSE.txt')
MAX_PACKAGE=16*1024*1024


def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',value):raise ValueError('Use a numeric three-part plugin version')
    return tuple(map(int,value.split('.')))


def root(home):
    home=manager.real_directory(Path(home).expanduser().absolute(),create=True)
    shared=manager.real_directory(home/'shared',create=True)
    return manager.real_directory(shared/'premiere',create=True)


@contextlib.contextmanager
def lock(home):
    base=root(home)
    fd=os.open(base/'install.lock',os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'a+') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another Premiere package operation is running') from None
        yield base


def read_state(base):
    p=base/'state.json'
    if p.is_symlink():raise ValueError('Package state cannot be a symlink')
    return json.loads(p.read_text()) if p.exists() else {'schema':1,'current':None,'previous':None,'attempt':None}


def build_ids(directory):
    result={}
    for name in ('main','native','commands'):
        text=(directory/(name+'.js')).read_text()
        match=re.search(r"^const BUILD_ID=['\"]([^'\"]+)['\"];",text,re.M)
        stripped=re.sub(r'^const BUILD_ID=.*?;\n','',text,flags=re.M)
        expected=name+'-'+hashlib.sha256(stripped.encode()).hexdigest()[:12]
        if not match or match[1]!=expected:raise ValueError('Stale module build stamp: '+name)
        result[name]=expected
    manifest=json.loads((directory/'manifest.json').read_text())
    result.update(plugin_id=manifest['id'],plugin_version=manifest['version'])
    return result


def validate_manifest(manifest):
    if not isinstance(manifest,dict) or not isinstance(manifest.get('host'),dict):raise ValueError('Invalid plugin manifest shape')
    if manifest.get('name')!='Field Lab':raise ValueError('Unexpected plugin display name')
    if manifest.get('id')!=PLUGIN_ID or manifest.get('host',{}).get('app')!='premierepro' or manifest.get('manifestVersion')!=5:raise ValueError('Unexpected plugin identity or host')
    version(manifest.get('version'))
    permissions=manifest.get('requiredPermissions',{})
    if not isinstance(permissions,dict) or set(permissions)-{'localFileSystem','network'}:raise ValueError('Unreviewed additional plugin permissions')
    network=permissions.get('network',{})
    if not isinstance(network,dict) or set(network)-{'domains'} or not isinstance(network.get('domains',[]),list):raise ValueError('Unexpected network permissions')
    if manifest.get('main')!='index.html' or manifest.get('requiredPermissions',{}).get('localFileSystem')!='request':raise ValueError('Unexpected plugin entry point or filesystem permissions')
    for domain in manifest.get('requiredPermissions',{}).get('network',{}).get('domains',[]):
        if not isinstance(domain,str) or not re.fullmatch(r'http://127\.0\.0\.1:\d+',domain):raise ValueError('Package must not add external network permission')


def stage(home,plugin_version,source=SOURCE,fault=None):
    version(plugin_version);source=manager.real_directory(source)
    with lock(home) as base:
        candidates=manager.real_directory(base/'candidates',create=True)
        contents={}
        for rel in FILES:
            p=manager.safe_relative(source,rel)
            if not p.is_file():raise ValueError('Missing plugin runtime file: '+rel)
            contents[rel]=p.read_bytes()
        manifest=json.loads(contents['manifest.json']);manifest['version']=plugin_version;validate_manifest(manifest)
        contents['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
        seal={rel:hashlib.sha256(data).hexdigest() for rel,data in sorted(contents.items())}
        candidate_id=hashlib.sha256(json.dumps(seal,sort_keys=True).encode()).hexdigest()
        dest=candidates/candidate_id
        if dest.exists():return describe(verify_candidate(dest))
        temp=candidates/('.staging-'+uuid.uuid4().hex);temp.mkdir();plugin=temp/'plugin';plugin.mkdir()
        for rel,data in contents.items():
            p=plugin/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            with p.open('rb') as stream:os.fsync(stream.fileno())
            if fault:fault('copy',p)
        record={'schema':1,'candidate_id':candidate_id,'plugin_id':PLUGIN_ID,'version':plugin_version,'files':seal,'plugin_build':build_ids(plugin)}
        manager.atomic_json(temp/'candidate.json',record)
        for directory,_,_ in os.walk(temp,topdown=False):manager.sync_dir(directory)
        os.rename(temp,dest);manager.sync_dir(candidates)
        return describe(verify_candidate(dest))


def candidate_path(home,candidate_id):
    if not re.fullmatch('[0-9a-f]{64}',candidate_id):raise ValueError('Invalid candidate ID')
    return manager.real_directory(root(home)/'candidates'/candidate_id)


def verify_candidate(path):
    path=manager.real_directory(path)
    record_path=manager.safe_relative(path,'candidate.json')
    r=json.loads(record_path.read_text())
    if r.get('schema')!=1 or r.get('plugin_id')!=PLUGIN_ID or set(r.get('files',{}))!=set(FILES):raise ValueError('Invalid candidate seal')
    if hashlib.sha256(json.dumps(r['files'],sort_keys=True).encode()).hexdigest()!=r.get('candidate_id') or path.name!=r['candidate_id']:raise ValueError('Candidate identity mismatch')
    plugin=manager.real_directory(path/'plugin')
    entries=list(plugin.rglob('*'))
    if any(p.is_symlink() for p in entries):raise ValueError('Candidate runtime cannot contain symlinks')
    actual={p.relative_to(plugin).as_posix() for p in entries if p.is_file()}
    if actual!=set(FILES):raise ValueError('Candidate contains missing or unsealed runtime files')
    for rel,sha in r['files'].items():
        p=manager.safe_relative(plugin,rel)
        if manager.digest(p)!=sha:raise ValueError('Candidate content changed: '+rel)
    manifest=json.loads((plugin/'manifest.json').read_text());validate_manifest(manifest)
    if manifest['version']!=r['version'] or build_ids(plugin)!=r['plugin_build']:raise ValueError('Candidate build/version mismatch')
    return {**r,'path':str(path)}


def describe(r):
    return {'candidate_id':r['candidate_id'],'version':r['version'],'plugin_build':r['plugin_build'],'manifest':str(Path(r['path'])/'plugin/manifest.json'),'package_output':str(Path(r['path'])/'adobe-package'),'next':'Run premiere package --candidate '+r['candidate_id']+' (pinned Adobe SDK), or UDT Package then attach-ccx'}


def inspect_ccx(ccx,record):
    ccx=Path(ccx)
    if ccx.is_symlink() or not ccx.is_file() or ccx.stat().st_size>MAX_PACKAGE:raise ValueError('Expected a regular bounded CCX file')
    with zipfile.ZipFile(ccx) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(infos)>64:raise ValueError('CCX has too many entries')
        if len(set(names))!=len(names):raise ValueError('CCX contains duplicate entries')
        for info in infos:
            p=PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Unsafe CCX entry')
        if sum(i.file_size for i in infos)>MAX_PACKAGE:raise ValueError('CCX uncompressed payload exceeds limit')
        manifests=[n for n in names if n=='manifest.json' or n.endswith('/manifest.json')]
        if len(manifests)!=1:raise ValueError('CCX requires one runtime manifest')
        prefix=manifests[0][:-len('manifest.json')]
        manifest=json.loads(archive.read(manifests[0]));validate_manifest(manifest)
        if manifest['version']!=record['version']:raise ValueError('CCX version differs from candidate')
        payload=set()
        for rel,sha in record['files'].items():
            name=prefix+rel
            if name not in names:raise ValueError('CCX is missing candidate payload: '+rel)
            if rel=='manifest.json':
                # Adobe's own packager normalizes JSON whitespace and omits the
                # source file's terminal newline. Require exact semantics.
                staged=json.loads((Path(record['path'])/'plugin/manifest.json').read_text())
                if json.loads(archive.read(name))!=staged:raise ValueError('CCX manifest differs from sealed candidate')
            elif hashlib.sha256(archive.read(name)).hexdigest()!=sha:raise ValueError('CCX differs from sealed candidate: '+rel)
            payload.add(name)
        extra=[i.filename for i in infos if not i.is_dir() and i.filename not in payload]
        if extra:raise ValueError('Unsealed CCX payload: '+', '.join(extra))
    return {'sha256':manager.digest(ccx),'bytes':ccx.stat().st_size}


def attach(home,candidate_id,ccx,fault=None):
    with lock(home):
        path=candidate_path(home,candidate_id);r=verify_candidate(path);seal=inspect_ccx(ccx,r)
        temp=path/('.package-'+uuid.uuid4().hex)
        shutil.copyfile(ccx,temp)
        with temp.open('rb') as stream:os.fsync(stream.fileno())
        if manager.digest(temp)!=seal['sha256']:raise ValueError('CCX changed during copy')
        if fault:fault('package_copy',temp)
        target=path/'plugin.ccx'
        if target.exists() and manager.digest(target)!=seal['sha256']:raise ValueError('Candidate already has a different sealed CCX')
        os.replace(temp,target);manager.sync_dir(path)
        package={**seal,'candidate_id':candidate_id,'packaging_method':'Operator-supplied Adobe CCX; runtime payload verified','attached_at':time.time()}
        manager.atomic_json(path/'package.json',package)
        return {'candidate_id':candidate_id,'ccx':str(target),**package}


def package_adobe(home,candidate_id,offline=False,node=None,npm=None,runner=subprocess.run):
    """Use the pinned, unmodified Adobe SDK package implementation on arm64.

    Maintainer build step only. It does not enable devtools, load a plugin,
    communicate with Premiere, or implement an alternative archive format.
    """
    path=candidate_path(home,candidate_id)
    if (path/'package.json').exists():
        record,ccx=verified_package(home,candidate_id)
        return {'candidate_id':candidate_id,'ccx':str(ccx),'reused':True}
    node=node or shutil.which('node');npm=npm or shutil.which('npm')
    if not node or not npm:raise ValueError('Maintainer packaging requires local Node and npm')
    tooling=Path(__file__).resolve().parents[1]/'development/premiere-packager'
    with lock(home):
        record=verify_candidate(path)
        output=manager.real_directory(path/'adobe-package',create=True)
        install=[str(npm),'ci','--ignore-scripts','--no-audit','--no-fund']+(['--offline'] if offline else [])
        runner(install,cwd=tooling,check=True,capture_output=True,text=True,timeout=120)
        result=runner([str(node),str(tooling/'package.cjs'),str(path/'plugin/manifest.json'),str(output)],cwd=tooling,check=True,capture_output=True,text=True,timeout=120)
        report=json.loads(result.stdout)
        if report.get('sdk')!='@adobe/uxp-devtools-core' or report.get('version')!='1.2.0' or report.get('method')!='PluginPackageCommand.package':raise ValueError('Unexpected Adobe SDK build report')
        ccx=output/(PLUGIN_ID+'_premierepro.ccx')
        verify_candidate(path);seal=inspect_ccx(ccx,record)
        manager.atomic_json(path/'build-provenance.json',{'sdk':report['sdk'],'version':report['version'],'method':report['method'],'npm_lock_sha256':manager.digest(tooling/'package-lock.json'),'wrapper_sha256':manager.digest(tooling/'package.cjs'),'ccx_sha256':seal['sha256'],'built_at':time.time(),'validation':'Strict Field Lab source/permission/payload checks; host install verified separately'})
    return attach(home,candidate_id,ccx)


def verified_package(home,candidate_id):
    path=candidate_path(home,candidate_id);r=verify_candidate(path)
    p=manager.safe_relative(path,'package.json');package=json.loads(p.read_text())
    ccx=manager.safe_relative(path,'plugin.ccx');actual=inspect_ccx(ccx,r)
    if package.get('candidate_id')!=candidate_id or actual!={k:package[k] for k in ('sha256','bytes')}:raise ValueError('CCX package seal mismatch')
    return r,ccx


def host_running():
    result=subprocess.run(['/bin/ps','-axo','comm='],capture_output=True,text=True,check=True,timeout=10)
    return any('Adobe Premiere' in Path(line.strip()).name for line in result.stdout.splitlines())


def upia(command,tool=UPIA):
    tool=Path(tool)
    if tool.is_symlink() or not tool.is_file():raise ValueError('Adobe UPIA is unavailable')
    result=subprocess.run([str(tool),*command],capture_output=True,text=True,timeout=120)
    return {'returncode':result.returncode,'stdout':result.stdout[-50000:],'stderr':result.stderr[-50000:]}


def listed(listing,plugin_version):
    # UPIA is human-readable and omits plugin IDs. This is an installation
    # observation only; a native receipt independently verifies runtime builds.
    return any(re.fullmatch(r'\s*Enabled\s+Field Lab\s+'+re.escape(plugin_version)+r'\s*',line) for line in listing['stdout'].splitlines())


def reconcile(home,tool=UPIA,runner=None):
    runner=runner or (lambda command:upia(command,tool))
    with lock(home) as base:
        state=read_state(base);attempt=state.get('attempt')
        if not attempt:return {'state':state,'reconciled':False}
        r,ccx=verified_package(home,attempt['candidate_id']);listing=runner(['--list','all'])
        if listing['returncode'] or not listed(listing,r['version']):
            return {'state':state,'reconciled':False,'message':'Unknown installer outcome; no automatic reinstall or removal','listing':listing}
        if state.get('current')!=r['candidate_id']:state['previous']=state.get('current')
        state.update(current=r['candidate_id'],attempt=None,runtime_verified=False,last_install={**attempt,'reconciled_at':time.time(),'listing':listing})
        manager.atomic_json(base/'state.json',state)
        return {'state':state,'reconciled':True,'message':'Adobe lists the installed version; verify a fresh native saved receipt'}


def install(home,candidate_id,premiere_closed=False,tool=UPIA,runner=None,running=None,fault=None):
    runner=runner or (lambda command:upia(command,tool));running=running or host_running
    with lock(home) as base:
        r,ccx=verified_package(home,candidate_id);state=read_state(base)
        if not premiere_closed or running():raise ValueError('Save projects and quit Premiere before installing; pass --premiere-closed only after checking')
        if state.get('attempt'):raise ValueError('An installer outcome is unknown; run premiere reconcile first')
        if state.get('current')==candidate_id:
            listing=runner(['--list','all'])
            if listing['returncode'] or not listed(listing,r['version']):
                state['runtime_verified']=False;state['registration_drift']={'at':time.time(),'listing':listing}
                manager.atomic_json(base/'state.json',state)
                return {'state':state,'reused':False,'message':'Adobe registration differs from the ledger; review before reinstalling'}
            return {'state':state,'reused':True}
        if state.get('current'):
            old=verify_candidate(candidate_path(home,state['current']))
            if version(r['version'])<=version(old['version']):raise ValueError('Adobe updates require an increased version; package previous source with a new version to roll back code')
        attempt={'candidate_id':candidate_id,'version':r['version'],'started_at':time.time(),'id':uuid.uuid4().hex}
        state['attempt']=attempt;manager.atomic_json(base/'state.json',state)
        if fault:fault('before_install',state)
        result=runner(['--install',str(ccx)])
        if fault:fault('after_install',state)
        attempt['result']=result;manager.atomic_json(base/'state.json',state)
        if result['returncode']:return {'state':state,'message':'Adobe installation did not confirm success; reconcile before retrying'}
        listing=runner(['--list','all'])
        if listing['returncode'] or not listed(listing,r['version']):return {'state':state,'listing':listing,'message':'Installer returned success but native registration is unverified; reconcile'}
        state.update(previous=state.get('current'),current=candidate_id,attempt=None,runtime_verified=False,last_install={**attempt,'listing':listing})
        manager.atomic_json(base/'state.json',state)
        return {'state':state,'message':'Installed; reopen Premiere and verify a fresh native saved receipt'}


def verify_receipt_operation(path,receipt,started):
    if tuple(path.parts[-4:])!=('_DIT_NOTES','editorial','bridge','receipt.json'):
        raise ValueError('Use the actual project bridge receipt, not a copied evidence file')
    database=path.parents[2]/'project.sqlite3'
    if database.is_symlink() or not database.is_file():raise ValueError('Project bridge database unavailable')
    with contextlib.closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as db:
        config=db.execute('SELECT value FROM premiere_bridge_config WHERE id=1').fetchone()
        operation=db.execute('SELECT desired_hash,status,receipt,created_at FROM premiere_bridge_operations WHERE operation_id=?',(receipt['operation_id'],)).fetchone()
    if not config or not operation:raise ValueError('Receipt operation is not registered in this project')
    config=json.loads(config[0])
    if config.get('project_id')!=receipt.get('project_id') or config.get('native_project_path')!=receipt.get('native_project_path'):
        raise ValueError('Native receipt pairing differs from project registration')
    if operation[0]!=receipt.get('desired_hash') or operation[1]!='saved' or operation[3]<started:
        raise ValueError('Require a fresh operation created after installation and reconciled as saved')
    if not operation[2]:raise ValueError('Native receipt differs from durable acknowledgement')
    durable=json.loads(operation[2])
    # Idempotent host replay generates a new envelope ID; its saved project
    # evidence must still be identical to the first durable acknowledgement.
    evidence=lambda packet:{key:value for key,value in packet.items() if key!='receipt_id'}
    if evidence(durable)!=evidence(receipt):raise ValueError('Native receipt differs from durable acknowledgement')


def verify_runtime(home,candidate_id,receipt_path,operation_id):
    with lock(home) as base:
        r,_=verified_package(home,candidate_id);state=read_state(base)
        if state.get('attempt') or state.get('current')!=candidate_id:raise ValueError('Candidate is not the confirmed installed version')
        receipt_path=Path(receipt_path).expanduser().resolve(strict=True);receipt=json.loads(receipt_path.read_text())
        started=state.get('last_install',{}).get('started_at',state.get('last_install',{}).get('at',0))
        if receipt_path.stat().st_mtime<started:raise ValueError('Native receipt predates this package installation')
        if receipt.get('protocol')!=1 or receipt.get('status')!='saved' or receipt.get('operation_id')!=operation_id:raise ValueError('Require the fresh requested operation saved by the native host')
        verify_receipt_operation(receipt_path,receipt,started)
        if receipt.get('plugin_build')!=r['plugin_build']:raise ValueError('Running plugin build/version does not match installed package')
        if not receipt.get('native_project_path') or not receipt.get('project_id') or not receipt.get('readback',{}).get('native_project_guid'):raise ValueError('Incomplete native save evidence')
        state['runtime_verified']={'at':time.time(),'candidate_id':candidate_id,'operation_id':operation_id,'receipt_sha256':manager.digest(receipt_path),'plugin_build':r['plugin_build']}
        manager.atomic_json(base/'state.json',state)
        return {'state':state}


def status(home,tool=UPIA):
    base=root(home)
    return {'state':read_state(base),'premiere_running':host_running(),'upia':str(tool),'upia_available':Path(tool).is_file(),'scope':'Plugin only; media, project databases, OffShoot and CCC untouched'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--home',type=Path,default=manager.DEFAULT_HOME)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('stage');p.add_argument('--version',required=True);p.add_argument('--source',type=Path,default=SOURCE)
    p=sub.add_parser('package');p.add_argument('--candidate',required=True);p.add_argument('--offline',action='store_true')
    p=sub.add_parser('attach-ccx');p.add_argument('--candidate',required=True);p.add_argument('--ccx',type=Path,required=True)
    p=sub.add_parser('install');p.add_argument('--candidate',required=True);p.add_argument('--premiere-closed',action='store_true')
    sub.add_parser('reconcile');sub.add_parser('status')
    p=sub.add_parser('verify-runtime');p.add_argument('--candidate',required=True);p.add_argument('--receipt',type=Path,required=True);p.add_argument('--operation-id',required=True)
    for command in sub.choices.values():command.add_argument('--home',type=Path,default=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        if args.command=='stage':value=stage(args.home,args.version,args.source)
        elif args.command=='package':value=package_adobe(args.home,args.candidate,args.offline)
        elif args.command=='attach-ccx':value=attach(args.home,args.candidate,args.ccx)
        elif args.command=='install':value=install(args.home,args.candidate,args.premiere_closed)
        elif args.command=='reconcile':value=reconcile(args.home)
        elif args.command=='verify-runtime':value=verify_runtime(args.home,args.candidate,args.receipt,args.operation_id)
        else:value=status(args.home)
        print(json.dumps(value,indent=2));return 0
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError,zipfile.BadZipFile,KeyError,sqlite3.Error) as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
