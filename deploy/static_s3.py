#!/usr/bin/env python3
"""Deploy a sealed static review through private S3 + CloudFront signed cookies.

All AWS operations use the selected AWS CLI profile. No AWS credential is copied
into the site. Signing keys/access links stay in a separate mode-0700 state folder.
"""
from pathlib import Path
import argparse,json,os,subprocess,time,uuid,base64

def write(path,data,mode=0o600):
 path=Path(path);tmp=path.with_suffix('.pending');tmp.write_text(json.dumps(data,indent=2) if not isinstance(data,str) else data);os.chmod(tmp,mode);os.replace(tmp,path)

def aws(profile,service,operation,**fields):
 cmd=['aws','--profile',profile,'--region','us-east-1' if service in ('cloudfront','logs') else fields.pop('_region','us-west-1'),service,operation,'--output','json','--no-cli-pager']
 for key,value in fields.items():
  cmd.append('--'+key.replace('_','-'));cmd.append(json.dumps(value) if isinstance(value,(dict,list)) else str(value))
 r=subprocess.run(cmd,capture_output=True,text=True)
 if r.returncode:raise RuntimeError(service+' '+operation+': '+r.stderr.strip())
 return json.loads(r.stdout) if r.stdout.strip() else {}

def provision(profile,region,state_dir):
 state_dir=Path(state_dir).expanduser().resolve();state_dir.mkdir(parents=True,exist_ok=True);os.chmod(state_dir,0o700);path=state_dir/'deployment.json';s=json.loads(path.read_text()) if path.exists() else {}
 def save():write(path,s)
 identity=aws(profile,'sts','get-caller-identity',_region=region);account=identity['Account']
 if s.get('account') not in (None,account):raise ValueError('Deployment belongs to another AWS account')
 s.update(account=account,region=region,profile=profile);s.setdefault('name','field-lab-review-'+time.strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:8]);s.setdefault('bucket',s['name']+'-'+account);s.setdefault('audit_bucket',s['bucket']+'-audit');s.setdefault('snapshot','snapshots/initial');save()
 for name in ('bucket','audit_bucket'):
  if not s.get(name+'_created'):
   args={'bucket':s[name],'object_ownership':'BucketOwnerEnforced','_region':region}
   if region!='us-east-1':args['create_bucket_configuration']={'LocationConstraint':region}
   aws(profile,'s3api','create-bucket',**args);s[name+'_created']=True;save()
  aws(profile,'s3api','put-public-access-block',bucket=s[name],public_access_block_configuration={'BlockPublicAcls':True,'IgnorePublicAcls':True,'BlockPublicPolicy':True,'RestrictPublicBuckets':True},_region=region)
  aws(profile,'s3api','put-bucket-encryption',bucket=s[name],server_side_encryption_configuration={'Rules':[{'ApplyServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'},'BucketKeyEnabled':True}]},_region=region)
  aws(profile,'s3api','put-bucket-tagging',bucket=s[name],tagging={'TagSet':[{'Key':'Project','Value':'FieldLabStaticReview'},{'Key':'ManagedBy','Value':'field-lab-deployed'}]},_region=region)
 aws(profile,'s3api','put-bucket-versioning',bucket=s['bucket'],versioning_configuration={'Status':'Enabled'},_region=region)
 aws(profile,'s3api','put-bucket-lifecycle-configuration',bucket=s['bucket'],lifecycle_configuration={'Rules':[{'ID':'AbortIncompleteUploads','Status':'Enabled','Filter':{'Prefix':''},'AbortIncompleteMultipartUpload':{'DaysAfterInitiation':1}}]},_region=region)
 private=state_dir/'signing-key.pem';public=state_dir/'signing-key-public.pem'
 if not private.exists() and s.get('public_key_id'):raise ValueError('Registered signing key is missing; restore its private key or explicitly rotate the CloudFront key group')
 if not private.exists():
  from cryptography.hazmat.primitives.asymmetric import rsa
  from cryptography.hazmat.primitives import serialization
  key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
  private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(private,0o600)
  public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
 if 'public_key_id' not in s:
  r=aws(profile,'cloudfront','create-public-key',public_key_config={'CallerReference':s['name'],'Name':s['name'],'EncodedKey':public.read_text(),'Comment':'Static review access signing'});s['public_key_id']=r['PublicKey']['Id'];save()
 if 'key_group_id' not in s:
  r=aws(profile,'cloudfront','create-key-group',key_group_config={'Name':s['name'],'Items':[s['public_key_id']],'Comment':'Private Field Lab review'});s['key_group_id']=r['KeyGroup']['Id'];save()
 if 'oac_id' not in s:
  r=aws(profile,'cloudfront','create-origin-access-control',origin_access_control_config={'Name':s['name'],'Description':'Private S3 static review','SigningProtocol':'sigv4','SigningBehavior':'always','OriginAccessControlOriginType':'s3'});s['oac_id']=r['OriginAccessControl']['Id'];save()
 if 'cache_policy_id' not in s:
  r=aws(profile,'cloudfront','create-cache-policy',cache_policy_config={'Name':s['name'],'Comment':'Immutable snapshot with origin-controlled short HTML caching','DefaultTTL':86400,'MaxTTL':31536000,'MinTTL':0,'ParametersInCacheKeyAndForwardedToOrigin':{'EnableAcceptEncodingGzip':True,'EnableAcceptEncodingBrotli':True,'HeadersConfig':{'HeaderBehavior':'none'},'CookiesConfig':{'CookieBehavior':'none'},'QueryStringsConfig':{'QueryStringBehavior':'none'}}});s['cache_policy_id']=r['CachePolicy']['Id'];save()
 if 'headers_policy_id' not in s:
  csp="default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
  r=aws(profile,'cloudfront','create-response-headers-policy',response_headers_policy_config={'Name':s['name'],'Comment':'Static review HTTPS and browser isolation','CustomHeadersConfig':{'Quantity':1,'Items':[{'Header':'Cache-Control','Value':'private, no-store','Override':True}]},'SecurityHeadersConfig':{'StrictTransportSecurity':{'AccessControlMaxAgeSec':31536000,'IncludeSubdomains':True,'Override':True},'ContentTypeOptions':{'Override':True},'FrameOptions':{'FrameOption':'DENY','Override':True},'ReferrerPolicy':{'ReferrerPolicy':'no-referrer','Override':True},'ContentSecurityPolicy':{'ContentSecurityPolicy':csp,'Override':True}}});s['headers_policy_id']=r['ResponseHeadersPolicy']['Id'];save()
 if 'distribution_id' not in s:
  behavior={'TargetOriginId':'private-s3','ViewerProtocolPolicy':'https-only','AllowedMethods':{'Quantity':2,'Items':['GET','HEAD'],'CachedMethods':{'Quantity':2,'Items':['GET','HEAD']}},'CachePolicyId':s['cache_policy_id'],'ResponseHeadersPolicyId':s['headers_policy_id'],'Compress':True,'TrustedKeyGroups':{'Enabled':True,'Quantity':1,'Items':[s['key_group_id']]}}
  public_behaviors=[{**behavior,'PathPattern':path,'TrustedKeyGroups':{'Enabled':False,'Quantity':0}} for path in ('access.html','access.js')]
  config={'CallerReference':s['name'],'Comment':'Field Lab private static interview review','Enabled':True,'DefaultRootObject':'index.html','Origins':{'Quantity':1,'Items':[{'Id':'private-s3','DomainName':s['bucket']+'.s3.'+region+'.amazonaws.com','OriginPath':'/'+s['snapshot'],'OriginAccessControlId':s['oac_id'],'S3OriginConfig':{'OriginAccessIdentity':''}}]},'DefaultCacheBehavior':behavior,'CacheBehaviors':{'Quantity':2,'Items':public_behaviors},'ViewerCertificate':{'CloudFrontDefaultCertificate':True},'PriceClass':'PriceClass_100','HttpVersion':'http2and3','IsIPV6Enabled':True,'Restrictions':{'GeoRestriction':{'RestrictionType':'none','Quantity':0}}}
  r=aws(profile,'cloudfront','create-distribution',distribution_config=config);s.update(distribution_id=r['Distribution']['Id'],domain=r['Distribution']['DomainName'],distribution_arn=r['Distribution']['ARN']);save()
 policy={'Version':'2012-10-17','Statement':[{'Sid':'CloudFrontReadOnly','Effect':'Allow','Principal':{'Service':'cloudfront.amazonaws.com'},'Action':'s3:GetObject','Resource':'arn:aws:s3:::'+s['bucket']+'/*','Condition':{'StringEquals':{'AWS:SourceArn':s['distribution_arn'],'AWS:SourceAccount':account}}},{'Sid':'DenyNonTLS','Effect':'Deny','Principal':'*','Action':'s3:*','Resource':['arn:aws:s3:::'+s['bucket'],'arn:aws:s3:::'+s['bucket']+'/*'],'Condition':{'Bool':{'aws:SecureTransport':'false'}}}]}
 aws(profile,'s3api','put-bucket-policy',bucket=s['bucket'],policy=policy,_region=region)
 # Standard logging v2. Cookie values and query strings are deliberately omitted.
 if not s.get('logging'):
  source=s['name']+'-source';dest=s['name']+'-logs'
  logpolicy={'Version':'2012-10-17','Statement':[{'Sid':'LogDelivery','Effect':'Allow','Principal':{'Service':'delivery.logs.amazonaws.com'},'Action':'s3:PutObject','Resource':'arn:aws:s3:::'+s['audit_bucket']+'/*','Condition':{'StringEquals':{'aws:SourceAccount':account},'ArnLike':{'aws:SourceArn':'arn:aws:logs:us-east-1:'+account+':delivery-source:*'}}},{'Sid':'LogACLCheck','Effect':'Allow','Principal':{'Service':'delivery.logs.amazonaws.com'},'Action':'s3:GetBucketAcl','Resource':'arn:aws:s3:::'+s['audit_bucket'],'Condition':{'StringEquals':{'aws:SourceAccount':account},'ArnLike':{'aws:SourceArn':'arn:aws:logs:us-east-1:'+account+':delivery-source:*'}}}]}
  aws(profile,'s3api','put-bucket-policy',bucket=s['audit_bucket'],policy=logpolicy,_region=region)
  aws(profile,'logs','put-delivery-source',name=source,resource_arn=s['distribution_arn'],log_type='ACCESS_LOGS')
  d=aws(profile,'logs','put-delivery-destination',name=dest,delivery_destination_configuration={'destinationResourceArn':'arn:aws:s3:::'+s['audit_bucket']+'/cloudfront'},output_format='json')
  delivery=aws(profile,'logs','create-delivery',delivery_source_name=source,delivery_destination_arn=d['deliveryDestination']['arn'],record_fields=['date','time','x-edge-location','sc-bytes','c-ip','cs-method','cs-uri-stem','sc-status','x-edge-result-type','time-taken','ssl-protocol','ssl-cipher'])
  s['logging']=delivery['delivery']['id'];save()
 return s

def access(state_dir,site=None,days=7):
 state_dir=Path(state_dir)
 if site and state_dir.resolve().is_relative_to(Path(site).resolve()):raise ValueError('Signing state must be outside the exported site')
 if not 0<days<=7:raise ValueError('Access links must expire within seven days')
 state_dir=Path(state_dir);s=json.loads((state_dir/'deployment.json').read_text());expiry=int(time.time()+days*86400)
 policy=json.dumps({'Statement':[{'Resource':'https://'+s['domain']+'/*','Condition':{'DateLessThan':{'AWS:EpochTime':expiry}}}]},separators=(',',':')).encode()
 from cryptography.hazmat.primitives import hashes,serialization
 from cryptography.hazmat.primitives.asymmetric import padding
 key=serialization.load_pem_private_key((state_dir/'signing-key.pem').read_bytes(),password=None)
 signature=key.sign(policy,padding.PKCS1v15(),hashes.SHA1())
 def enc(v):return base64.b64encode(v).decode().replace('+','-').replace('=','_').replace('/','~')
 values={'CloudFront-Policy':enc(policy),'CloudFront-Signature':enc(signature),'CloudFront-Key-Pair-Id':s['public_key_id']}
 url='https://'+s['domain']+'/access.html#'+__import__('urllib.parse',fromlist=['urlencode']).urlencode(values)
 write(state_dir/'access.json',{'url':url,'expires':expiry,'cookies':values});write(state_dir/'OPEN-REVIEW.html','<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><a href="'+url.replace('&','&amp;')+'">Open private Field Lab review (expires in seven days)</a>')
 if site:
  site=Path(site)
  write(site/'access.html','<!doctype html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Field Lab access</title><script type="module" src="/access.js"></script></head><body style="font:16px Helvetica,Arial,sans-serif;padding:32px"><p id="status">Open this page using your private access link.</p></body></html>',0o644)
  write(site/'access.js',"const p=new URLSearchParams(location.hash.slice(1));const names=['CloudFront-Policy','CloudFront-Signature','CloudFront-Key-Pair-Id'];if(names.every(n=>/^[A-Za-z0-9_~.=-]+$/.test(p.get(n)||''))){for(const n of names)document.cookie=n+'='+p.get(n)+'; Path=/; Secure; SameSite=Lax';location.replace('/');}",0o644)
 return {'domain':s['domain'],'expires':expiry,'access_file':str(state_dir/'OPEN-REVIEW.html')}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['provision','access','upload']);p.add_argument('--profile',required=True);p.add_argument('--region',default='us-west-1');p.add_argument('--state',type=Path,required=True);p.add_argument('--site',type=Path);a=p.parse_args()
 if a.site and a.state.resolve().is_relative_to(a.site.resolve()):raise ValueError('Signing state must be outside the exported site')
 if a.command=='provision':s=provision(a.profile,a.region,a.state);print(json.dumps({k:s[k] for k in ('bucket','distribution_id','domain','logging')}))
 elif a.command=='access':print(json.dumps(access(a.state,a.site)))
 else:
  s=json.loads((a.state/'deployment.json').read_text());assert a.site and (a.site/'data/vectors.json').is_file() and (a.site/'index.html').is_file()
  cmd=['aws','--profile',a.profile,'--region',a.region,'s3','sync',str(a.site),'s3://'+s['bucket']+'/'+s['snapshot']+'/','--only-show-errors','--cache-control','public,max-age=86400']
  subprocess.run(cmd,check=True)
  for name in ('index.html','access.html','access.js'):
   subprocess.run(['aws','--profile',a.profile,'--region',a.region,'s3','cp',str(a.site/name),'s3://'+s['bucket']+'/'+s['snapshot']+'/'+name,'--only-show-errors','--cache-control','no-store','--content-type','application/javascript' if name.endswith('.js') else 'text/html'],check=True)
  import gzip,tempfile
  with tempfile.TemporaryDirectory(prefix='fieldlab-upload-') as tmp:
   compressed=Path(tmp)/'vectors.json'
   with (a.site/'data/vectors.json').open('rb') as src,gzip.open(compressed,'wb',compresslevel=6) as dst:
    __import__('shutil').copyfileobj(src,dst)
   subprocess.run(['aws','--profile',a.profile,'--region',a.region,'s3','cp',str(compressed),'s3://'+s['bucket']+'/'+s['snapshot']+'/data/vectors.json','--only-show-errors','--content-type','application/json','--content-encoding','gzip','--cache-control','public,max-age=86400'],check=True)
  print(json.dumps({'uploaded':True,'bucket':s['bucket'],'domain':s['domain']}))
if __name__=='__main__':main()
