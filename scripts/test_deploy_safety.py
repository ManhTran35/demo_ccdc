"""Eight isolated deployment-script checks; every gcloud call is mocked."""
from pathlib import Path
import json, os, shutil, subprocess, tempfile
source = Path(__file__).resolve().parents[1]
base = Path(tempfile.mkdtemp(prefix='ccdc-deploy-check.'))
repo = base / 'repo'; repo.mkdir()
for f in ['Dockerfile','.dockerignore','.gcloudignore','app.py','core.py','ui_labels.py','requirements-lock.txt']:
 shutil.copy2(source/f,repo/f)
shutil.copytree(source/'services',repo/'services',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
(repo/'scripts').mkdir(); shutil.copy2(source/'scripts/deploy.sh',repo/'scripts/deploy.sh')
(repo/'private').mkdir();(repo/'private/source.xlsx').write_bytes(b'FAKE PRIVATE');(repo/'.env').write_text('FAKE=PRIVATE');(repo/'keys.json').write_text('{"fake":"PRIVATE"}')
for cmd in [['git','init','-q'],['git','add','.'],['git','-c','user.name=Test','-c','user.email=test@example.com','commit','-qm','deployment-safety-fixture']]:
 subprocess.run(cmd,cwd=repo,check=True,stdout=subprocess.DEVNULL)
binpath=base/'bin';binpath.mkdir()
mock=binpath/'gcloud';mock.write_text('''#!/usr/bin/env python3
import json,os,sys,pathlib
args=sys.argv[1:]
with open(os.environ['MOCK_LOG'],'a') as f:f.write(json.dumps(args)+'\\n')
if args==['run','deploy','--help']:
 print('--[no-]allow-unauthenticated --[no-]invoker-iam-check --[no-]cpu-throttling '+('' if os.environ.get('MOCK_OLD_SDK') else '--[no-]iap'));sys.exit()
if args[:3]==['billing','projects','describe']:print('{"billingEnabled":true}')
elif args[:3]==['storage','buckets','describe']:
 print(json.dumps({'public_access_prevention':'inherited' if os.environ.get('MOCK_UNSAFE_BUCKET') else 'enforced','uniform_bucket_level_access':True}))
elif args[:2]==['builds','submit']:
 p=pathlib.Path(args[2]);found=[str(f.relative_to(p)) for f in p.rglob('*') if f.is_file()]
 assert found and not any(f.endswith(('.xlsx','.json','.html','.zip')) or f.startswith(('.env','.git','private/')) for f in found),found
 tag=next(v.split('=',1)[1] for v in args if v.startswith('--tag='))
 print(json.dumps({'status':'SUCCESS','results':{'images':[{'name':tag,'digest':'sha256:'+'a'*64}]}}))
elif args[:2]==['run','deploy']:
 assert all(flag in args for flag in ['--iap','--invoker-iam-check','--no-allow-unauthenticated','--cpu-throttling']),args
 assert any('@sha256:' in v for v in args if v.startswith('--image=')),args
 env=json.load(open(next(v.split('=',1)[1] for v in args if v.startswith('--env-vars-file='))))
 assert env['STORAGE_BACKEND']=='gcs' and env['APP_ENV']=='staging' and json.loads(env['VALIDATION_WARNING_LIMITS'])=={},env
elif args[:3]==['run','services','describe']:
 print(json.dumps({'metadata':{'annotations':{'run.googleapis.com/iap-enabled':'true'}},'status':{'latestReadyRevisionName':'test-ready'}}))
elif args[:3]==['run','services','get-iam-policy']:
 print(json.dumps({'bindings':[{'members':['allUsers']}]} if os.environ.get('MOCK_PUBLIC_IAM') else {'bindings':[]}))
else:print('mock-existing-resource')
''');mock.chmod(0o700)
env=os.environ.copy(); env.update(PATH=str(binpath)+':'+env['PATH'],GCP_PROJECT_ID='ccdc-test',GCS_BUCKET='ccdc-data-test',RUNTIME_SERVICE_ACCOUNT='runtime@ccdc-test.iam.gserviceaccount.com',ADMIN_EMAILS='approved@example.com',IAP_AUDIENCE='verified-test-audience',APP_ENV='staging',ARTIFACT_REGISTRY_REPOSITORY='ccdc-images',BUILD_SERVICE_ACCOUNT='projects/ccdc-test/serviceAccounts/build@ccdc-test.iam.gserviceaccount.com',BUILD_SOURCE_BUCKET='ccdc-source-test',BUILD_LOG_BUCKET='ccdc-logs-test',CLOUD_RESOURCES_APPROVED='true',STAGING_SYNTHETIC_ONLY='true')
cases=[('missing-approval',{'CLOUD_RESOURCES_APPROVED':''},False,'CLOUD_RESOURCES_APPROVED'),('production-unapproved',{'APP_ENV':'production'},False,'DATA_CLOUD_APPROVED'),('same-runtime-build',{'BUILD_SERVICE_ACCOUNT':'projects/ccdc-test/serviceAccounts/runtime@ccdc-test.iam.gserviceaccount.com'},False,'must be separate'),('old-sdk',{'MOCK_OLD_SDK':'1'},False,'Upgrade'),('unsafe-bucket',{'MOCK_UNSAFE_BUCKET':'1'},False,'Public Access Prevention'),('valid-staging',{},True,'Revision: test-ready'),('public-iam',{'MOCK_PUBLIC_IAM':'1'},False,'broad Cloud Run IAM')]
for name,changes,success,message in cases:
 e=env.copy();e.update(changes,MOCK_LOG=str(base/(name+'.calls')),DEPLOY_RECORD_DIR=str(base/(name+'-records')))
 r=subprocess.run(['bash','scripts/deploy.sh'],cwd=repo,env=e,capture_output=True,text=True)
 assert (r.returncode==0)==success,(name,r.returncode,r.stdout,r.stderr)
 assert message in r.stdout+r.stderr,(name,r.stdout,r.stderr)
 print(name,'PASS')
(repo/'core.py').write_text((repo/'core.py').read_text()+'\n# dirty test\n')
e=env.copy();e.update(MOCK_LOG=str(base/'dirty.calls'),DEPLOY_RECORD_DIR=str(base/'dirty-records'))
r=subprocess.run(['bash','scripts/deploy.sh'],cwd=repo,env=e,capture_output=True,text=True)
assert r.returncode and 'working tree must be clean' in r.stderr and not (base/'dirty.calls').exists()
print('dirty-source','PASS')
print('8 deployment safety checks passed; all GCP commands mocked; test archive excluded deliberately tracked fake private files. Evidence:',base)
