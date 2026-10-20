import json,subprocess,tempfile,pathlib,stat
exe='/Volumes/seed2/Projects/stateless-native-parity/validation/corpus/target/debug/native-evidence'
d=pathlib.Path(tempfile.mkdtemp(prefix='stateless-rust-evidence-smoke-'));p=d/'witness'
def run(*args):
 r=subprocess.run([exe,*map(str,args)],check=True,text=True,capture_output=True);v=json.loads(r.stdout);(d/(args[0]+'.receipt.json')).write_text(json.dumps(v,indent=2,ensure_ascii=False));return v
record=run('record',p);replay=run('replay',str(p)+'.v2',d/'roundtrip.v2');legacy=run('read-v1',str(p)+'.v1');upgrade=run('upgrade',str(p)+'.v1',d/'upgrade.v2');diag=run('diagnostic',str(p)+'.v2',d/'diagnostic.json')
assert record['main_counts']==dict(initial_state=0,reducer=3,state=4,transition=3)
assert all(record['controls'].values()),record['controls']
for v in (record,replay,upgrade):
 r=v['report'];assert r['outcome']=='exact' and r['steps_verified']==3 and r['failure_reproduced'] and r['build_matches'],r
 assert v['retained_config']==record['retained_config']
assert record['evidence']==replay['evidence']
assert (d/'roundtrip.v2').read_bytes()==pathlib.Path(str(p)+'.v2').read_bytes()
assert legacy['execution_calls']==0 and legacy['initial_phase']=='initial_state' and legacy['turn_phase'] is None,legacy
assert diag['execution_calls']==0 and diag['no_overwrite'] and diag['diagnostic_text']==record['diagnostic_text']
assert (d/'diagnostic.json').read_text()==record['diagnostic_text']
assert all(not row['causal']['causes'] for row in upgrade['evidence']['steps'])
for f in (pathlib.Path(str(p)+'.v2'),d/'roundtrip.v2',d/'upgrade.v2',d/'diagnostic.json'):assert stat.S_IMODE(f.stat().st_mode)&0o077==0
assert record['legacy_encoder_calls']==0 and record['codec_into_calls']>0
print(json.dumps(dict(artifact_directory=str(d),main_counts=record['main_counts'],controls=record['controls'],commands=5,exact_byte_roundtrip=True,explicit_upgrade=True,retained_config=record['retained_config'])))
