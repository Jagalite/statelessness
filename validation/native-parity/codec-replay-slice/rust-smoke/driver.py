import json,subprocess,tempfile,pathlib
exe='/Volumes/seed2/Projects/stateless-native-parity/validation/corpus/target/debug/native-codec-replay'
d=pathlib.Path(tempfile.mkdtemp(prefix='stateless-rust-codec-smoke-')); p=d/'witness'
def run(*args):
 r=subprocess.run([exe,*map(str,args)],check=True,text=True,capture_output=True); v=json.loads(r.stdout); (d/(args[0]+'.receipt.json')).write_text(json.dumps(v,indent=2)); return v
record=run('record',p); replay=run('replay',str(p)+'.bin'); portable=run('replay-json',str(p)+'.json')
for v in (record,replay):
 assert v['rich']==v['bounded'],v
 report=v['rich']['report']; assert report['outcome']=='exact' and report['steps_verified']==3 and report['failure_reproduced'] and report['build_matches'],report
 assert v['legacy_encoder_calls']==0 and v['codec_into_calls']>0
assert record['rich']==replay['rich']
assert record['retained_config']==replay['retained_config']
c=record['controls']
for k in ('clear_entry','sticky_finish','framed_shared_budget','nested_framing','oversize_actual_retained','rich_divergence','static_borrow'): assert c[k] is True,(k,c)
assert c['preflight_decode_calls']==0 and c['oversize_reducer_calls']==1 and c['legacy_encoder_calls']==0 and c['codec_into_calls']>0,c
assert portable['outcome']=='exact' and portable['steps_verified']==3 and portable['failure_reproduced'] and portable['legacy_encoder_calls']==0,portable
print(json.dumps({'artifact_directory':str(d),'sessions':4,'portable_replay':True,'controls':c,'retained_config':record['retained_config']}))
