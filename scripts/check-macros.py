#!/usr/bin/env python3
"""Cold-offline extracted-package consumers and compile diagnostic fixtures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import math
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import time
import sys
import uuid
from qualification_context import git_context
from qualify import source_hashes as qualification_source_hashes

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--allow-dirty', action='store_true')
parser.add_argument('--evidence', type=Path)
parser.add_argument('--compile-only', action='store_true', help='Compile positive/negative fixtures without executing Rust')
parser.add_argument('--kyoto', type=Path, help='Queue each visible Cargo command; requires --compile-only')
parser.add_argument('--timeout', type=float, default=1800, help='Per-command queue+compile deadline')
args = parser.parse_args()
if args.kyoto and not args.compile_only:
    parser.error('--kyoto requires --compile-only; runtime wrappers cannot bypass queue policy')
if not math.isfinite(args.timeout) or args.timeout <= 0:
    parser.error('--timeout must be positive')
results = []
def source_hashes():
    return qualification_source_hashes(root)
initial_hashes = source_hashes()
archive_hashes = {}
cleanup_safe = True
log_directory = None
command_failure = None
compiler_identity = None
progress_writer = None
CANCEL_WAIT_SECONDS = 30

def queued(command, cwd, env):
    prefix = [sys.executable, str(args.kyoto.resolve())]
    def invoke(arguments):
        result = subprocess.run(prefix + arguments, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    job = None
    receipt = {'exit': None, 'status': 'setup_error', 'output': '', 'cleanup_safe': False}
    invocation_owner = str(uuid.uuid4())
    receipt['kyoto_owner'] = invocation_owner
    started = time.monotonic()
    terminal = {'succeeded', 'failed', 'cancelled'}

    def validate(value, identity=None):
        if (not isinstance(value, dict) or not isinstance(value.get('id'), str)
                or value.get('status') not in terminal | {'queued', 'running'}
                or (identity is not None and value['id'] != identity)):
            raise ValueError('malformed or changed Kyoto job identity/status')
        return value

    def capture_output(value):
        if value is not None and isinstance(value.get('log'), str):
            path = Path(value['log'])
            if path.is_file():
                return path.read_text(errors='replace')
        return ''

    def cancel_and_confirm():
        # Cancellation is asynchronous: only a subsequent terminal observation
        # proves the job no longer needs its cwd. An uncertain result retains it.
        receipt['cancellation_requested'] = []
        receipt['cancellation_terminal'] = {}
        receipt['cancellation_errors'] = []
        try:
            owned = [job] if job is not None else invoke(['jobs', '--user-id', invocation_owner])
            if not isinstance(owned, list) or not owned:
                raise ValueError('no terminal confirmation for the interrupted submission')
            candidates = [validate(candidate) for candidate in owned]
            deadline = time.monotonic() + CANCEL_WAIT_SECONDS
            for candidate in candidates:
                identity = candidate['id']
                if candidate['status'] not in terminal:
                    invoke(['cancel', identity, '--user-id', invocation_owner])
                    receipt['cancellation_requested'].append(identity)
                while True:
                    latest = validate(invoke(['show', identity]), identity)
                    if latest['status'] in terminal:
                        receipt['cancellation_terminal'][identity] = latest['status']
                        receipt['output'] = capture_output(latest) or receipt['output']
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('macro cancellation terminal status remains unconfirmed')
                    time.sleep(min(1, max(0, deadline - time.monotonic())))
            receipt['cleanup_safe'] = len(receipt['cancellation_terminal']) == len(candidates)
        except BaseException as error:
            receipt['cancellation_errors'].append(str(error) or type(error).__name__)

    try:
        job = validate(invoke(['submit', '--user-id', invocation_owner, '--cwd', str(cwd), '--', *command]))
        identity = job['id']
        receipt['kyoto_job'] = identity
        while job['status'] in ('queued', 'running'):
            if time.monotonic() - started >= args.timeout:
                raise TimeoutError('macro compile queue deadline reached')
            time.sleep(2)
            job = validate(invoke(['show', identity]), identity)
        receipt.update(kyoto_status=job['status'], cleanup_safe=True, output=capture_output(job))
        if job['status'] == 'cancelled':
            raise RuntimeError('macro compilation was cancelled')
        code = job.get('exit_code')
        if type(code) is not int or (job['status'] == 'succeeded') != (code == 0):
            raise RuntimeError('macro compilation terminal status/exit code is inconsistent')
        receipt.update(exit=code, status='finished')
    except BaseException as error:
        receipt.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'setup_error',
                       error=str(error) or type(error).__name__)
        receipt['output'] = capture_output(job) or receipt['output']
        if not receipt['cleanup_safe']:
            cancel_and_confirm()
    return receipt


def run(command, cwd, env, expected=None):
    global cleanup_safe, command_failure
    started = time.monotonic()
    if args.compile_only and command[:2] == ['cargo', 'run']:
        command = ['cargo', 'check', *command[2:]]
    if args.compile_only and (command[:2] not in (['cargo', 'check'], ['cargo', 'package']) or (command[1] == 'package' and '--no-verify' not in command)):
        raise RuntimeError(f'non-compilation command rejected by compile-only macro harness: {command}')
    try:
        if args.kyoto:
            result = queued(command, cwd, env)
        else:
            completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=args.timeout)
            result = {'exit': completed.returncode, 'output': completed.stdout + completed.stderr,
                      'status': 'finished', 'cleanup_safe': True, 'process_started': True}
    except BaseException as error:
        result = {'exit': None, 'output': str(error), 'error': str(error) or type(error).__name__,
                  'status': 'interrupted' if isinstance(error, KeyboardInterrupt) else 'setup_error',
                  'cleanup_safe': False, 'process_started': isinstance(error, subprocess.TimeoutExpired)}
    cleanup_safe = cleanup_safe and result['cleanup_safe']
    output = result.pop('output', '')
    valid = result['status'] == 'finished' and (
        result['exit'] != 0 and expected in output if expected is not None else result['exit'] == 0)
    if result['status'] == 'finished':
        result['status'] = 'passed' if valid else 'failed'
    entry = {**result, 'command': command, 'seconds': time.monotonic()-started,
             'expected_diagnostic': expected,
             'runtime_executed': not args.compile_only and command[:2] == ['cargo', 'run'] and result.get('process_started', False)}
    if log_directory is not None:
        log = log_directory / f'{len(results):03d}.log'
        log.write_text(output, encoding='utf-8')
        entry.update(log=str(log), log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
    else:
        entry['output'] = output
    results.append(entry)
    if not valid:
        command_failure = result.get('error') or f'qualification command did not satisfy its exit/diagnostic gate: {command}'
    if progress_writer is not None:
        progress_writer('running' if valid else 'failed', command_failure if not valid else None)
    if not valid:
        raise RuntimeError(command_failure + '\n' + output)
    return output

def fixtures(temp, env):
    for manifest in [root/'Cargo.toml', root/'crates/statelessness-macros/Cargo.toml']:
        command = ['cargo','package','--manifest-path',str(manifest),'--locked','--offline','--no-verify']
        if args.allow_dirty:
            command += ['--allow-dirty']
        run(command, root, env)
    for archive in sorted((temp/'target/package').glob('*.crate')):
        archive_hashes[archive.name] = hashlib.sha256(archive.read_bytes()).hexdigest()
        with tarfile.open(archive) as source:
            if any("__pycache__" in Path(member.name).parts or member.name.endswith(".pyc")
                   for member in source.getmembers()):
                raise RuntimeError("Generated Python cache leaked into package")
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
    if 'statelessness_macros' in output or 'statelessness-macros' in output:
        raise RuntimeError('Core-only consumer unexpectedly compiled macros: ' + output)
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
#[derive(macros::ModelCodec)] #[model_codec(crate="::engine",version=1)]
struct ManualCodec<T>(T) where T:Clone+Eq;
impl<T:Clone+Eq> engine::Model for ManualCodec<T> {
 type State=T;type Input=T;type Output=();
 fn metadata(&self)->engine::ModelMetadata{Adapter.metadata()}
 fn initial_state(&self)->Result<T,engine::ModelError>{Ok(self.0.clone())}
 fn step(&self,_:&T,input:&T)->Result<engine::Transition<T,()>,engine::ModelError>{Ok(engine::Transition::accepted(input.clone(),vec![]))}
 fn check_state(&self,_:&T)->Result<Vec<engine::Check>,engine::ModelError>{Ok(vec![])}
}
#[derive(macros::ModelCodec)] #[model_codec(crate="::engine",version=1)]
struct Callable<F> where F: Fn() { callback:F }
impl<F:Fn()> engine::Model for Callable<F> {
 type State=u8;type Input=u8;type Output=();
 fn metadata(&self)->engine::ModelMetadata{Adapter.metadata()}
 fn initial_state(&self)->Result<u8,engine::ModelError>{(self.callback)();Ok(0)}
 fn step(&self,_:&u8,input:&u8)->Result<engine::Transition<u8,()>,engine::ModelError>{Ok(engine::Transition::accepted(*input,vec![]))}
 fn check_state(&self,_:&u8)->Result<Vec<engine::Check>,engine::ModelError>{Ok(vec![])}
}
#[derive(macros::ModelCodec)] #[model_codec(crate="::engine",version=1)]
struct UnitWhere where fn(): Fn();
impl engine::Model for UnitWhere {
 type State=u8;type Input=u8;type Output=();
 fn metadata(&self)->engine::ModelMetadata{Adapter.metadata()}
 fn initial_state(&self)->Result<u8,engine::ModelError>{Ok(0)}
 fn step(&self,_:&u8,input:&u8)->Result<engine::Transition<u8,()>,engine::ModelError>{Ok(engine::Transition::accepted(*input,vec![]))}
 fn check_state(&self,_:&u8)->Result<Vec<engine::Check>,engine::ModelError>{Ok(vec![])}
}
struct DefaultAdapter;
#[model(crate="::engine",state=u8,input=u8,output=(),unchecked,initial_default,generate)]
impl DefaultAdapter {
 #[stateless(metadata)] fn identity(&self)->engine::ModelMetadata{Adapter.metadata()}
 #[stateless(step)] fn reduce(&self,_:&u8,input:&u8)->Result<engine::Transition<u8,()>,engine::ModelError>{Ok(engine::Transition::accepted(*input,vec![]))}
 #[stateless(inputs)] fn inputs(&self,_:&u8)->Result<Vec<u8>,engine::ModelError>{Ok(vec![1,2])}
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
 assert_eq!(engine::ModelCodec::encode_state(&ManualCodec(0u8),&7).unwrap(),[7]);
 assert_eq!(DefaultAdapter.initial_state().unwrap(),0);
 assert!(engine::Generate::generate(&DefaultAdapter,&0,&mut engine::Rng::new(1)).unwrap().is_some());
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
        'adapter_runtime_incompatible': ('mod old {pub mod modeling{pub const MACRO_API_V1:()=();}} struct S; #[model(crate="crate::old",state=u8,input=u8,output=(),unchecked,initial_default)] impl S {#[stateless(metadata)] fn metadata(&self){} #[stateless(step)] fn step(&self){} }', 'MACRO_API_V2'),
        'model_codec_unknown_option': ('#[derive(macros::ModelCodec)] #[model_codec(crate="::engine",unknown)] struct S;', 'SM400'),
        'model_codec_zero_version': ('#[derive(macros::ModelCodec)] #[model_codec(crate="::engine",version=0)] struct S;', 'SM401'),
        'model_codec_enum': ('#[derive(macros::ModelCodec)] #[model_codec(crate="::engine")] enum S {A}', 'SM402'),
        'async_callback': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked)] impl S {#[stateless(step)] async fn step(&self){} }', 'SM215'),
        'generic_callback': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked)] impl S {#[stateless(step)] fn step<T>(&self){} }', 'SM207'),
        'missing_metadata': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked,initial_default)] impl S {#[stateless(step)] fn step(&self){} }', 'SM212: metadata'),
        'initial_default_conflict': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked,initial_default)] impl S {#[stateless(metadata)] fn meta(&self){} #[stateless(initial)] fn init(&self){} #[stateless(step)] fn step(&self){} }', 'SM213'),
        'generation_without_inputs': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked,initial_default,generate)] impl S {#[stateless(metadata)] fn meta(&self){} #[stateless(step)] fn step(&self){} }', 'SM214'),
        'generation_options_without_generate': ('struct S; #[model(crate="::engine",state=u8,input=u8,output=(),unchecked,auto_options=())] impl S {}', 'SM214'),
        'runtime_incompatible': ('mod old {pub mod modeling {pub const MACRO_API_V2:()=();}} #[derive(TraceEncode)] #[trace(crate="crate::old")] struct S;', 'MACRO_API_V1'),
    }
    for name, (source, diagnostic) in failures.items():
        (consumer/'src/main.rs').write_text(prelude+source+'\nfn main(){}')
        run(['cargo','check','--offline','--quiet'], consumer, env, diagnostic)
        print('PASS compile-fail',name,diagnostic)


def write_evidence(destination, temp, env, status, error=None):
    try:
        current = source_hashes()
        unchanged = current == initial_hashes
        source_error = None
    except Exception as problem:
        current, unchanged, source_error = {}, False, str(problem)
    if not unchanged:
        status = 'failed'
        error = error or 'Source changed during qualification; rerun on a stable snapshot'
    context = git_context(root)
    report = {
        'status': status,
        'compile_only': args.compile_only,
        'runtime_qualification_complete': status == 'passed' and not args.compile_only,
        'compile_qualification_complete': status in ('passed', 'compiled_only'),
        'source_unchanged': unchanged,
        'source_sha256': initial_hashes,
        'source_after_sha256': current,
        'source_error': source_error,
        'build_environment': {name: env.get(name) for name in ('RUSTUP_TOOLCHAIN', 'RUSTC', 'RUSTDOC', 'CARGO_INCREMENTAL')},
        'compiler': compiler_identity,
        'revision': context['commit'],
        'git_provenance_available': context['available'],
        'archive_sha256': archive_hashes,
        'results': results,
        'temporary_directory': str(temp),
        'cleanup_safe': cleanup_safe,
        'temporary_directory_preserved': status not in ('passed', 'compiled_only') or not cleanup_safe,
        'error': error,
    }
    if status != 'running':
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_suffix(destination.suffix + '.tmp')
    pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    pending.replace(destination)
    return report


def main():
    global log_directory, compiler_identity, progress_writer
    destination = args.evidence.resolve() if args.evidence else None
    artifacts = destination.parent / (destination.stem + '-artifacts') if destination else None
    if destination and (destination.exists() or artifacts.exists()):
        parser.error('evidence or artifact directory already exists; choose a new --evidence path')
    temp = Path(tempfile.mkdtemp(prefix='stateless-macros-'))
    destination = destination or temp / 'qualification.json'
    log_directory = (artifacts or temp) / 'logs'
    log_directory.mkdir(parents=True)
    env = {**os.environ, 'CARGO_TARGET_DIR': str(temp / 'target'), 'CARGO_INCREMENTAL': '0'}
    progress_writer = lambda status, error=None: write_evidence(destination, temp, env, status, error)
    report = write_evidence(destination, temp, env, 'running')
    try:
        compiler = subprocess.run([env.get('RUSTC', 'rustc'), '--version'], env=env,
                                  capture_output=True, text=True, timeout=30)
        compiler_identity = {'exit_code': compiler.returncode, 'output': compiler.stdout + compiler.stderr}
        if compiler.returncode != 0:
            raise RuntimeError('compiler identity unavailable: ' + compiler_identity['output'])
        fixtures(temp, env)
        if not cleanup_safe:
            raise RuntimeError('A command has no confirmed terminal status')
        status, error = ('compiled_only' if args.compile_only else 'passed'), None
    except BaseException as problem:
        status, error = 'failed', str(problem) or type(problem).__name__
    finally:
        report = write_evidence(destination, temp, env, status, error)
        progress_writer = None
    if report['status'] in ('passed', 'compiled_only') and cleanup_safe:
        shutil.rmtree(temp)
    else:
        print(f'Macro qualification failed; temporary inputs and logs retained: {temp}', file=sys.stderr)
        print(f'Evidence: {destination}', file=sys.stderr)
        return 1
    print('Macro package and diagnostic compilation passed; Rust runtime not executed' if args.compile_only else 'Macro package and diagnostic qualification passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
