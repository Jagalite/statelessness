import json,subprocess,tempfile,pathlib
exe='/Volumes/seed2/Projects/stateless-native-parity/validation/corpus/target/debug/native-checked'
d=pathlib.Path(tempfile.mkdtemp(prefix='stateless-rust-checked-smoke-'));p=d/'witness'
def run(*args):
 r=subprocess.run([exe,*map(str,args)],check=True,capture_output=True,text=True);v=json.loads(r.stdout);(d/(args[0]+'.receipt.json')).write_text(json.dumps(v,indent=2));return v
record=run('record',p);replay=run('replay',str(p)+'.bin');portable=run('replay-json',str(p)+'.json')
for v in (record,replay):
 assert v['rich']==v['bounded'];r=v['rich']['report'];assert r['outcome']=='exact' and r['steps_verified']==3 and r['failure_reproduced'] and r['build_matches'],r
 assert v['legacy_encoder_calls']==0 and v['codec_into_calls']>0
assert record['rich']==replay['rich'] and record['retained_config']==replay['retained_config']
assert record['main_counts']==dict(initial_state=0,reducer=3,state=4,transition=3)
assert record['checked']==dict(initial_checks=2,state_check_count=2,full_checking=True,sequence='1')
for k,v in record['controls'].items():assert v is (False if k in ('attach_rechecks','handoff_rechecks') else True),(k,v)
ring=record['ring'];assert ring['rich']==ring['bounded'];r=ring['rich']['report'];assert r['outcome']=='exact' and r['steps_verified']==1 and not r['failure_reproduced'],r
assert portable['outcome']=='exact' and portable['steps_verified']==3 and portable['failure_reproduced'] and portable['legacy_encoder_calls']==0
print(json.dumps(dict(artifact_directory=str(d),main_counts=record['main_counts'],checked=record['checked'],controls=record['controls'],sessions=6,portable_replay=True,retained_config=record['retained_config'])))
