#!/usr/bin/env python3
"""Cold-offline extracted-package consumers and compile diagnostic fixtures."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
from qualification_context import git_context

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--allow-dirty', action='store_true')
parser.add_argument('--evidence', type=Path)
args = parser.parse_args()
results = []
files = sorted([*root.glob('src/**/*.rs'), *root.glob('tests/**/*.rs'), *root.glob('crates/**/*.rs'), *root.glob('crates/**/Cargo.toml'), root/'Cargo.toml', root/'Cargo.lock', root/'build.rs', Path(__file__).resolve(), root/'scripts/qualification_context.py', root/'scripts/test_qualification_context.py'])
def source_hashes():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
initial_hashes = source_hashes()
archive_hashes = {}

def run(command, cwd, env, expected=None):
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    results.append({'command': command, 'exit': result.returncode, 'seconds': time.monotonic()-started, 'expected_diagnostic': expected})
    if expected:
        assert result.returncode != 0 and expected in output, output
    else:
        assert result.returncode == 0, output
    return output

with tempfile.TemporaryDirectory(prefix='stateless-macros-') as directory:
    temp = Path(directory)
    env = {**os.environ, 'CARGO_TARGET_DIR': str(temp/'target'), 'CARGO_INCREMENTAL': '0'}
    for manifest in [root/'Cargo.toml', root/'crates/statelessness-macros/Cargo.toml']:
        command = ['cargo','package','--manifest-path',str(manifest),'--locked','--offline','--no-verify']
        if args.allow_dirty:
            command += ['--allow-dirty']
        run(command, root, env)
    for archive in sorted((temp/'target/package').glob('*.crate')):
        archive_hashes[archive.name] = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tarfile.open(archive) as source:
            assert not any("__pycache__" in Path(member.name).parts or member.name.endswith(".pyc")
                           for member in source.getmembers()), "Generated Python cache leaked into package"
            source.extractall(temp/'extracted', filter='data')
    core = next((temp/'extracted').glob('statelessness-[0-9]*'))
    macros = next((temp/'extracted').glob('statelessness-macros-*'))
    consumer = temp/'consumer'
    (consumer/'src').mkdir(parents=True)
    # Empty cache/registry verifies actual resolution, not a warm --offline build.
    env['CARGO_HOME'] = str(temp/'empty-cargo-home')
    base = '[package]\nname="macro-consumer"\nversion="0.0.0"\nedition="2024"\n[dependencies]\nengine={package="statelessness",path='+json.dumps(str(core), ensure_ascii=False)+'}\n'
    (consumer/'Cargo.toml').write_text(base, encoding='utf-8')
    (consumer/'src/main.rs').write_text('fn main(){let _=engine::Rng::new(0);}')
    output = run(['cargo','check','--offline','-v'], consumer, env)
    assert 'statelessness_macros' not in output and 'statelessness-macros' not in output, output
    (consumer/'Cargo.toml').write_text(base+'macros={package="statelessness-macros",path='+json.dumps(str(macros), ensure_ascii=False)+'}\n', encoding='utf-8')
    prelude = 'use macros::{model, TraceEncode, TraceDecode, input_domain};\n'
    passed = '''
use engine::value_codec::{TraceEncode as _,TraceDecode as _};
#[derive(Clone,PartialEq,Eq,TraceEncode,TraceDecode)]
#[trace(crate="::engine")]
struct Generic<T: Clone> where T: PartialEq + Eq { r#type:T, #[trace(length="u8")] values:Vec<u16> }
#[derive(TraceEncode,TraceDecode)] #[trace(crate="::engine",tag_type="u16")]
enum Event { #[trace(tag=400)] Unit, #[trace(tag=12)] Tuple(u8), #[trace(tag=7)] Named { value:u16 } }
#[derive(TraceEncode,TraceDecode)] #[trace(crate="::engine")] struct Tuple<T>(T);
#[derive(TraceEncode,TraceDecode)] #[trace(crate="::engine")] struct Unit;
#[derive(TraceEncode)] #[trace(crate="::engine")] struct Conditional { #[cfg(any())] excluded:u8, #[cfg(all())] included:u8 }
#[derive(Clone,PartialEq,Eq,TraceEncode)] #[trace(crate="::engine")] struct Output(u8);
#[derive(TraceEncode,TraceDecode)] #[trace(crate="::engine")] struct Array<const N:usize>{value:[u8;N]}
struct GenericAdapter<T>(std::marker::PhantomData<T>);
#[model(crate="::engine",state=T,input=T,output=(),unchecked)]
impl<T:Clone+Eq+Default> GenericAdapter<T> {
 #[stateless(metadata)] fn meta(&self)->engine::ModelMetadata{Adapter.metadata()}
 #[stateless(initial)] fn init(&self)->Result<T,engine::ModelError>{Ok(T::default())}
 #[stateless(step)] fn reduce(&self,_:&T,i:&T)->Result<engine::Transition<T,()>,engine::ModelError>{Ok(engine::Transition::accepted(i.clone(),vec![]))}
}
struct Adapter;
#[model(crate="::engine",state=u8,input=u8,output=Output,codec,unchecked)]
impl Adapter {
 #[stateless(metadata)] fn meta(&self)->engine::ModelMetadata {engine::demo::RequestModel::fixed().metadata()}
 #[stateless(initial)] fn init(&self)->Result<u8,engine::ModelError>{Ok(0)}
 #[stateless(step)] fn step(&self,s:&u8,i:&u8)->Result<engine::Transition<u8,Output>,engine::ModelError>{Ok(engine::Transition::accepted(s+i,vec![Output(*i)]))}
}
use engine::Model;
fn main(){
 assert_eq!(GenericAdapter::<u8>(std::marker::PhantomData).initial_state().unwrap(),0);
 assert_eq!(Array{value:[1u8,2]}.trace_bytes(2).unwrap(),[1,2]);
 assert!(macros::macro_build_id!().starts_with("macro-v1:"));
 assert_eq!(Conditional{included:3}.trace_bytes(10).unwrap(),[3]);
 let bytes=Generic{r#type:7u8,values:vec![2,3]}.trace_bytes(100).unwrap();assert_eq!(bytes,[7,2,2,0,3,0]);
 assert_eq!(Generic::<u8>::from_trace(&bytes,Default::default()).unwrap().r#type,7);
 assert_eq!(Event::Unit.trace_bytes(10).unwrap(),[144,1]);assert_eq!(Event::Unit.trace_variant(),Some("Unit"));
 assert!(Generic::<u8>::trace_schema().contains("values"));
 let _=Tuple(1u8).trace_bytes(10).unwrap();let _=Unit.trace_bytes(0).unwrap();assert_eq!(Adapter.initial_state().unwrap(),0);
}
'''
    (consumer/'src/main.rs').write_text(prelude+passed)
    run(['cargo','run','--offline','--quiet'], consumer, env)
    print('PASS renamed imports, generics, structs/enums, output encode-only, cold offline packages')
    failures = {
        'duplicate_tag': ('#[derive(TraceEncode)] #[trace(crate="::engine")] enum E {#[trace(tag=1)] A,#[trace(tag=1)] B}', 'SM107'),
        'unknown_field': ('#[derive(TraceEncode)] #[trace(crate="::engine")] struct S {#[trace(skip)] x:u8}', 'SM112'),
        'duplicate_field_option': ('#[derive(TraceEncode)] #[trace(crate="::engine")] struct S {#[trace(length="u8",length="u16")] x:Vec<u8>}', 'SM115'),
        'associated_item': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8,unchecked)] impl S {#[stateless(step)] const VALUE:u8=1; fn step(&self){} }', 'SM206'),
        'missing_tag': ('#[derive(TraceEncode)] #[trace(crate="::engine")] enum E {A}', 'SM106'),
        'unsupported_type': ('#[derive(TraceEncode)] #[trace(crate="::engine")] struct S(usize);', 'TraceEncode'),
        'conditional_method': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8,unchecked)] impl S {#[cfg(any())] #[stateless(step)] fn step(&self){} }', 'SM205'),
        'unknown_model': ('#[model(crate="::engine",magic)] impl S {}', 'SM200'),
        'unchecked_required': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8)] impl S {}', 'SM211'),
        'duplicate_id': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8)] impl S {#[stateless(state_check(id="x"))] fn a(&self){} #[stateless(state_check(id="x"))] fn b(&self){} }', 'SM208'),
        'duplicate_role': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8,unchecked)] impl S {#[stateless(step)] fn a(&self){} #[stateless(step)] fn b(&self){} }', 'SM210'),
        'bad_receiver': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=u8,unchecked)] impl S {#[stateless(step)] fn a(self){} }', 'SM207'),
        'domain_missing_variant': ('enum E{A,B} input_domain!{crate="::engine"; fn domain()->E {assumptions="test";limit=1;variants{E::A=>"included"};one E::A;}}', 'non-exhaustive'),
        'domain_missing_type': ('input_domain!{fn domain()->}', 'SM301'),
        'domain_no_assumptions': ('enum E{A} input_domain!{crate="::engine"; fn domain()->E {limit=1;variants{E::A=>"included"};one E::A;}}', 'SM306'),
        'runtime_incompatible': ('mod old {pub mod modeling {pub const MACRO_API_V2:()=();}} #[derive(TraceEncode)] #[trace(crate="crate::old")] struct S;', 'MACRO_API_V1'),
    }
    for name, (source, diagnostic) in failures.items():
        (consumer/'src/main.rs').write_text(prelude+source+'\nfn main(){}')
        run(['cargo','check','--offline','--quiet'], consumer, env, diagnostic)
        print('PASS compile-fail',name,diagnostic)

if args.evidence:
    hashes = source_hashes()
    assert hashes == initial_hashes, "Source changed during qualification; rerun on a stable snapshot"
    for entry in results:
        entry['command'] = [arg.replace(str(root), '<checkout>').replace(str(temp), '<temporary>') for arg in entry['command']]
    context = git_context(root)
    evidence = {'revision': context['commit'], 'git_provenance_available': context['available'], 'compiler': subprocess.check_output(['rustc','--version'],text=True).strip(), 'source_sha256':hashes, 'archive_sha256':archive_hashes, 'results':results}
    args.evidence.parent.mkdir(parents=True,exist_ok=True)
    args.evidence.write_text(json.dumps(evidence,indent=2)+'\n')
print('Macro package and diagnostic qualification passed')
