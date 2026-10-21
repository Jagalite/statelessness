#!/usr/bin/env python3
"""Focused N13 declaration rejection. Run this command through Kyoto only.

These are compiler rejections, not runtime rows. Each case must fail and contain
its independent diagnostic expectation. Full stdout/stderr bytes are retained.
"""
import hashlib,json,pathlib,subprocess,tempfile,time
ROOT=pathlib.Path(__file__).resolve().parents[4]
OUT=ROOT/'validation/native-parity/derive-model-v2/rust/compile-fail'/str(time.time_ns())
CASES={
 'duplicate_tag': ('#[derive(TraceEncode)] enum E {#[trace(tag=1)] A,#[trace(tag=1)] B}', 'SM107'),
 'missing_tag': ('#[derive(TraceEncode)] enum E { A }', 'SM106'),
 'overflow_tag': ('#[derive(TraceEncode)] enum E {#[trace(tag=256)] A}', 'SM107'),
 'negative_tag': ('#[derive(TraceEncode)] enum E {#[trace(tag=-1)] A}', 'SM106'),
 'u32_overflow_tag': ('#[derive(TraceEncode)] #[trace(tag_type="u32")] enum E {#[trace(tag=4294967296)] A}', 'SM106'),
 'unknown_type_option': ('#[derive(TraceEncode)] #[trace(unknown)] struct S;', 'SM100'),
 'invalid_tag_width': ('#[derive(TraceEncode)] #[trace(tag_type="u64")] enum E {#[trace(tag=1)] A}', 'SM102'),
 'struct_tag_width': ('#[derive(TraceEncode)] #[trace(tag_type="u16")] struct S;', 'SM102'),
 'discriminant': ('#[derive(TraceEncode)] enum E {#[trace(tag=1)] A=1}', 'SM109'),
 'skip': ('#[derive(TraceEncode)] struct S {#[trace(skip)] v:u8}', 'SM112'),
 'default': ('#[derive(TraceDecode)] struct S {#[trace(default)] v:u8}', 'SM112'),
 'duplicate_field': ('#[derive(TraceEncode)] struct S {#[trace(length="u8",length="u16")] v:String}', 'SM115'),
 'conflicting_field': ('#[derive(TraceEncode)] struct S {#[trace(length="u8",with="adapter")] v:String}', 'SM113'),
 'invalid_length': ('#[derive(TraceEncode)] struct S {#[trace(length="u64")] v:String}', 'SM111'),
 'missing_encode': ('struct No; #[derive(TraceEncode)] struct S(No);', 'TraceEncode'),
 'missing_decode': ('struct No; #[derive(TraceDecode)] struct S(No);', 'TraceDecode'),
 'unselected_missing_decode': ('struct No; #[derive(TraceDecode)] enum E {#[trace(tag=1)] Unit,#[trace(tag=2)] Other(No)}', 'TraceDecode'),
 'missing_length_encode': ('struct No; #[derive(TraceEncode)] struct S(#[trace(length="u8")] No);', 'LengthEncode'),
 'missing_length_decode': ('struct No; #[derive(TraceDecode)] struct S(#[trace(length="u8")] No);', 'LengthDecode'),
 'missing_adapter_decode': ('mod adapter {} #[derive(TraceDecode)] struct S(#[trace(with="adapter")] u8);', 'decode'),
 'model_zero': ('#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=0)] struct S;', 'SM401'),
 'model_overflow': ('#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=4294967296)] struct S;', 'SM401'),
 'model_unknown': ('#[derive(statelessness_macros::ModelCodec)] #[model_codec(unknown)] struct S;', 'SM400'),
 'model_duplicate': ('#[derive(statelessness_macros::ModelCodec)] #[model_codec(version=1,version=2)] struct S;', 'SM400'),
 'model_enum': ('#[derive(statelessness_macros::ModelCodec)] enum E { A }', 'SM402'),
}
def digest(p):
 b=p.read_bytes(); return {'path':str(p.relative_to(OUT.parent)),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def main():
 OUT.mkdir(parents=True,exist_ok=False)
 work=pathlib.Path(tempfile.mkdtemp(prefix='n13-compile-fail-'))
 (work/'src').mkdir()
 (work/'Cargo.toml').write_text('[package]\nname="n13-compile-fail"\nversion="0.0.0"\nedition="2024"\n[workspace]\n[dependencies]\nstateless={package="statelessness",path='+json.dumps(str(ROOT))+'}\nstatelessness-macros={path='+json.dumps(str(ROOT/'crates/statelessness-macros'))+'}\n')
 rows=[]
 for name,(source,expected) in CASES.items():
  (work/'src/main.rs').write_text('use statelessness_macros::{TraceEncode,TraceDecode};\n'+source+'\nfn main(){}\n')
  result=subprocess.run(['cargo','check','--offline','--quiet'],cwd=work,capture_output=True)
  (OUT/(name+'.rs')).write_text((work/'src/main.rs').read_text())
  log=OUT/(name+'.log'); log.write_bytes(result.stdout+result.stderr)
  required=[expected]
  if name in ('missing_encode','missing_decode','unselected_missing_decode','missing_length_encode','missing_length_decode'):
   required=['E0277', 'No: '+expected, 'not satisfied']
  elif name=='missing_adapter_decode': required=['E0425', 'cannot find function', 'decode', 'adapter']
  passed=result.returncode!=0 and all(part.encode() in log.read_bytes() for part in required)
  rows.append({'case':name,'expected':required,'exit_code':result.returncode,'passed':passed,**digest(log)})
  print('KYOTO_PROGRESS '+json.dumps({'completed':len(rows),'total':len(CASES),'phase':'compile-fail'}),flush=True)
 report={'scope':str(len(CASES))+' compile-fail declarations; no runtime execution','worktree':str(ROOT),'temporary_directory':str(work),'cases':rows,'passed':all(x['passed'] for x in rows)}
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 if not report['passed']: raise SystemExit(1)
if __name__=='__main__': main()
