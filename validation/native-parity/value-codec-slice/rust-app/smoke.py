import json,subprocess,tempfile,pathlib,hashlib,stat
root=pathlib.Path('/Volumes/seed2/Projects/stateless-native-parity');exe=root/'validation/corpus/target/debug/native-values';corpus=root/'conformance/canonical-value-codec-v1.json'
assert hashlib.sha256(corpus.read_bytes()).hexdigest()=='bf95caefe17b0920931a72cd232340c8eae03b23c0fee19183caee0b53b33a6c'
rows=json.loads(corpus.read_text())['value_cases'];d=pathlib.Path(tempfile.mkdtemp(prefix='stateless-rust-values-smoke-'));prefix=d/'witness'
def run(*args):
 r=subprocess.run([str(exe),*map(str,args)],check=True,text=True,capture_output=True);v=json.loads(r.stdout);(d/(args[0]+'.receipt.json')).write_text(json.dumps(v,indent=2,ensure_ascii=False));return v
values=run('values',d/'values.json');transcode=run('transcode',d/'values.json',d/'roundtrip.json');record=run('record',prefix);replay=run('replay',str(prefix)+'.v2',d/'roundtrip.v2')
expected=[dict(id=r['case'],hex=r['bytes_hex']) for r in rows];bundle=json.loads((d/'values.json').read_text());assert bundle['cases']==expected and json.loads((d/'roundtrip.json').read_text())==bundle
for v in (values,transcode):
 assert v['case_count']==83 and v['decoded_values']==[dict(id=r['case'],value=r['expected']['value']) for r in rows]
 c=v['controls'];assert c['exact_encode_caps']==83 and c['trailing_rejections']==83 and c['subview_decodes']==83 and c['owned_results']==83 and c['append_preserved'] and c['unit_budget_rejected'] and c['sticky_swallowed_error'] and c['direct_length_work_exact'],c
assert record['main_counts']==dict(initial_state=0,reducer=3,state=4,transition=3) and record['output_encode_only'] and record['legacy_encoder_calls']==0 and record['codec_into_calls']>0
for v in (record,replay):
 r=v['report'];assert r['outcome']=='exact' and r['steps_verified']==3 and r['failure_reproduced'] and r['build_matches'],r
assert record['evidence']==replay['evidence'] and record['retained_config']==replay['retained_config']
assert pathlib.Path(str(prefix)+'.v2').read_bytes()==(d/'roundtrip.v2').read_bytes()
t=record['evidence']['trace'];assert t['initial_state']=='00000000';one=b'\1'+(1).to_bytes(8,'little');output=b'\7'+(10).to_bytes(4,'little')+'\ufeffchanged'.encode()
for i,s in enumerate(t['steps'],1):assert s['input']==one.hex() and s['post_state']==(i.to_bytes(4,'little')+one*i).hex() and s['outputs']==[output.hex()]
for f in [d/'values.json',d/'roundtrip.json',pathlib.Path(str(prefix)+'.v2'),d/'roundtrip.v2']:assert stat.S_IMODE(f.stat().st_mode)&0o077==0
print(json.dumps(dict(artifact_directory=str(d),commands=4,value_cases=83,main_counts=record['main_counts'],exact_wire_goldens=True,output_encode_only=True,exact_v2_roundtrip=True,retained_config=record['retained_config'])))
