"""Independent read-only review of the private static sharing protocol.

All AWS methods are mocked; no account, bucket, distribution or credentials are
accessed. Cryptographic signatures and generated browser helper code are real.
"""
import base64,json,os,shutil,stat,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from deploy.static_s3 import access,provision


def decode(value):return base64.b64decode(value.replace('-','+').replace('_','=').replace('~','/'))


class StaticAccessProtocolReview(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.state=self.root/'state';self.state.mkdir(mode=0o700);self.site=self.root/'site';self.site.mkdir()
  self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
  private=self.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
  (self.state/'signing-key.pem').write_bytes(private);(self.state/'signing-key.pem').chmod(0o600)
  (self.state/'deployment.json').write_text(json.dumps({'domain':'review.example.cloudfront.net','public_key_id':'PUBLICKEYID','bucket':'private-fixture','snapshot':'snapshots/initial'}))

 def tearDown(self):self.tmp.cleanup()

 def test_bearer_policy_is_signed_for_exact_https_distribution_and_seven_days(self):
  before=time.time();result=access(self.state,self.site);saved=json.loads((self.state/'access.json').read_text());parts=urlsplit(saved['url'])
  self.assertEqual(parts.scheme,'https');self.assertEqual(parts.netloc,'review.example.cloudfront.net');self.assertEqual(parts.path,'/access.html');self.assertEqual(parts.query,'')
  values={k:v[0] for k,v in parse_qs(parts.fragment).items()}
  policy=decode(values['CloudFront-Policy']);document=json.loads(policy)
  self.assertEqual(document['Statement'][0]['Resource'],'https://review.example.cloudfront.net/*')
  expiry=document['Statement'][0]['Condition']['DateLessThan']['AWS:EpochTime']
  self.assertGreaterEqual(expiry,int(before+7*86400)-1);self.assertLessEqual(expiry,int(time.time()+7*86400)+1)
  self.key.public_key().verify(decode(values['CloudFront-Signature']),policy,padding.PKCS1v15(),hashes.SHA1())
  self.assertEqual(values['CloudFront-Key-Pair-Id'],'PUBLICKEYID');self.assertEqual(result['expires'],expiry)
  for filename in ['access.json','OPEN-REVIEW.html']:
   self.assertEqual(stat.S_IMODE((self.state/filename).stat().st_mode),0o600)

 def test_published_helpers_contain_no_signing_key_or_access_values(self):
  access(self.state,self.site);saved=json.loads((self.state/'access.json').read_text());public='\n'.join(p.read_text() for p in self.site.rglob('*') if p.is_file())
  self.assertNotIn('PRIVATE KEY',public);self.assertNotIn(saved['url'],public)
  for value in saved['cookies'].values():self.assertNotIn(value,public)
  self.assertEqual({p.name for p in self.site.iterdir()},{'access.html','access.js'})

 def browser_helper(self,fragment):
  node=shutil.which('node')
  if not node:self.skipTest('Node not installed')
  script="""const vm=require('node:vm'),fs=require('node:fs');let cookies=[],replaced=null;
const document={set cookie(v){cookies.push(v)}};const location={hash:process.argv[2],replace(v){replaced=v}};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,location,URLSearchParams});
process.stdout.write(JSON.stringify({cookies,replaced}));"""
  result=subprocess.run([node,'-e',script,str(self.site/'access.js'),fragment],capture_output=True,text=True,check=True)
  return json.loads(result.stdout)

 def test_helper_sets_only_three_host_cookies_and_removes_fragment(self):
  access(self.state,self.site);url=json.loads((self.state/'access.json').read_text())['url'];result=self.browser_helper('#'+urlsplit(url).fragment)
  self.assertEqual(len(result['cookies']),3);self.assertEqual(result['replaced'],'/')
  for cookie in result['cookies']:
   self.assertIn('; Path=/',cookie);self.assertIn('; Secure',cookie);self.assertIn('; SameSite=Lax',cookie);self.assertNotIn('; Domain=',cookie)

 def test_cookie_injection_or_missing_values_cannot_set_any_cookie(self):
  access(self.state,self.site)
  for fragment in ['', '#CloudFront-Policy=ok', '#CloudFront-Policy=ok%3B%20Domain%3Devil.example&CloudFront-Signature=ok&CloudFront-Key-Pair-Id=ok']:
   with self.subTest(fragment=fragment):self.assertEqual(self.browser_helper(fragment),{'cookies':[],'replaced':None})

 def test_signing_state_cannot_be_placed_inside_public_site(self):
  nested=self.site/'private-state';shutil.copytree(self.state,nested)
  with self.assertRaises(ValueError):access(nested,self.site)


 def test_expired_or_overlong_generation_is_refused(self):
  for days in [0,-1,7.01,float('inf'),float('nan')]:
   with self.subTest(days=days):
    with self.assertRaises(ValueError):access(self.state,self.site,days=days)
  self.assertFalse((self.state/'access.json').exists())

 def test_signature_does_not_validate_after_policy_expiry_is_tampered(self):
  from cryptography.exceptions import InvalidSignature
  access(self.state,self.site);saved=json.loads((self.state/'access.json').read_text());values=saved['cookies']
  policy=json.loads(decode(values['CloudFront-Policy']));policy['Statement'][0]['Condition']['DateLessThan']['AWS:EpochTime']+=86400
  changed=json.dumps(policy,separators=(',',':')).encode()
  with self.assertRaises(InvalidSignature):self.key.public_key().verify(decode(values['CloudFront-Signature']),changed,padding.PKCS1v15(),hashes.SHA1())

 def test_cli_upload_refuses_nested_signing_state_before_any_aws_call(self):
  import sys
  from deploy.static_s3 import main
  nested=self.site/'private-state';shutil.copytree(self.state,nested)
  args=['static_s3.py','upload','--profile','fixture','--state',str(nested),'--site',str(self.site)]
  with patch.object(sys,'argv',args),patch('deploy.static_s3.subprocess.run',side_effect=AssertionError('No AWS call allowed')) as request:
   with self.assertRaises(ValueError):main()
   request.assert_not_called()



class ProvisionSecurityReview(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.state=Path(self.tmp.name)/'state';self.calls=[]
 def tearDown(self):self.tmp.cleanup()
 def fake_aws(self,profile,service,operation,**fields):
  self.calls.append((service,operation,fields))
  responses={('sts','get-caller-identity'):{'Account':'123456789012'},('cloudfront','create-public-key'):{'PublicKey':{'Id':'pk'}},('cloudfront','create-key-group'):{'KeyGroup':{'Id':'kg'}},('cloudfront','create-origin-access-control'):{'OriginAccessControl':{'Id':'oac'}},('cloudfront','create-cache-policy'):{'CachePolicy':{'Id':'cache'}},('cloudfront','create-response-headers-policy'):{'ResponseHeadersPolicy':{'Id':'headers'}},('cloudfront','create-distribution'):{'Distribution':{'Id':'dist','DomainName':'review.example.cloudfront.net','ARN':'arn:aws:cloudfront::123456789012:distribution/dist'}},('logs','put-delivery-destination'):{'deliveryDestination':{'arn':'logs-dest'}},('logs','create-delivery'):{'delivery':{'id':'delivery'}}}
  return responses.get((service,operation),{})
 def provision(self):
  with patch('deploy.static_s3.aws',side_effect=self.fake_aws):return provision('fixture-profile','us-west-1',self.state)
 def call(self,operation):return next(fields for _,op,fields in self.calls if op==operation)

 def test_origin_is_private_read_only_and_authenticated_default_is_get_head_only(self):
  self.provision();config=self.call('create-distribution')['distribution_config'];default=config['DefaultCacheBehavior']
  self.assertTrue(default['TrustedKeyGroups']['Enabled']);self.assertEqual(default['TrustedKeyGroups']['Items'],['kg']);self.assertEqual(set(default['AllowedMethods']['Items']),{'GET','HEAD'})
  self.assertEqual(default['ViewerProtocolPolicy'],'https-only');self.assertEqual(config['Origins']['Items'][0]['OriginAccessControlId'],'oac')
  oac=self.call('create-origin-access-control')['origin_access_control_config'];self.assertEqual(oac['SigningBehavior'],'always');self.assertEqual(oac['SigningProtocol'],'sigv4')
  block=[fields for _,op,fields in self.calls if op=='put-public-access-block'];self.assertEqual(len(block),2)
  self.assertTrue(all(all(fields['public_access_block_configuration'].values()) for fields in block))
  policies=[fields['policy'] for _,op,fields in self.calls if op=='put-bucket-policy'];site=next(p for p in policies if any(s.get('Sid')=='CloudFrontReadOnly' for s in p['Statement']))
  read=next(s for s in site['Statement'] if s['Sid']=='CloudFrontReadOnly');self.assertEqual(read['Action'],'s3:GetObject');self.assertIn('AWS:SourceArn',read['Condition']['StringEquals']);self.assertIn('AWS:SourceAccount',read['Condition']['StringEquals'])
  log=self.call('create-delivery');self.assertNotIn('cs-cookie',log['record_fields']);self.assertNotIn('cs-uri-query',log['record_fields'])

 def test_public_paths_are_exact_bootstrap_files_not_a_broad_sensitive_prefix(self):
  self.provision();behaviors=self.call('create-distribution')['distribution_config']['CacheBehaviors']['Items']
  public={b['PathPattern'] for b in behaviors if not b['TrustedKeyGroups']['Enabled']}
  self.assertEqual(public,{'access.html','access.js'})

 def test_viewer_response_prevents_shared_cache_reuse_of_private_data(self):
  self.provision();headers=self.call('create-response-headers-policy')['response_headers_policy_config']
  custom=headers.get('CustomHeadersConfig',{}).get('Items',[]);cache=next((h['Value'] for h in custom if h['Header'].lower()=='cache-control'),'')
  self.assertIn('private',cache.lower());self.assertNotIn('public',cache.lower())

 def test_lost_signing_key_does_not_silently_generate_an_untrusted_replacement(self):
  self.provision();(self.state/'signing-key.pem').unlink();self.calls=[]
  with self.assertRaises(ValueError):self.provision()
  self.assertFalse((self.state/'signing-key.pem').exists())


if __name__=='__main__':unittest.main()
