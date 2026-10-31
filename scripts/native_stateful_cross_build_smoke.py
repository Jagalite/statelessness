"""Rust-to-Python observation comparison; not exact-build qualification.

Run after building the Rust fixture. The caller-attested file mapping below is
illustrative and does not prove the executing Python dependencies' provenance.
"""
import hashlib, json, os, runpy, subprocess, sys, tempfile
from pathlib import Path
from statelessness.trace_json import loads
from native_stateful_identity import verify_and_compare_cross_build
root=Path(__file__).resolve().parents[1]
binary=Path(os.environ.get('STATEFUL_RUST_BINARY', root/'target/debug/examples/native-stateful-network'))
with tempfile.TemporaryDirectory() as d:
    d=Path(d); trace_path=d/'rust.json'
    env=dict(os.environ,STATEFUL_RUST_BINARY=str(binary))
    subprocess.run([sys.executable,str(root/'examples/native-stateful/rust/bridge.py'),'record',str(trace_path)],env=env,check=True)
    trace=loads(trace_path.read_text())
    sys.argv=['network.py','record',str(d/'native.json')]
    model=runpy.run_path(str(root/'examples/native-stateful/python/network.py'))['m']
    meta=model.metadata()
    fields=('name','model_version','properties_version','codec_version')
    semantic={k:getattr(meta,k) for k in fields}
    files={'rs':root/'examples/native-stateful/rust/network.rs','rb':binary,'ps':root/'examples/native-stateful/python/network.py','pa':root/'python/src/statelessness/network.py'}
    def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
    manifest={'version':1,'semantic':semantic,'producer':{'build':trace.metadata.build,'source':{'rs':h(files['rs'])},'artifact':{'rb':h(files['rb'])}},'consumer':{'build':meta.build,'source':{'ps':h(files['ps'])},'artifact':{'pa':h(files['pa'])}}}
    raw=json.dumps(manifest,sort_keys=True).encode()
    result=verify_and_compare_cross_build(model,trace,raw,hashlib.sha256(raw).hexdigest(),files)
    print('strict=',result.strict_report,'observations=',result.observation_report)
    assert result.strict_report.outcome=='incompatible' and not result.strict_report.build_matches
    assert result.observation_report.outcome=='exact' and not result.observation_report.build_matches and result.observation_report.failure_reproduced
