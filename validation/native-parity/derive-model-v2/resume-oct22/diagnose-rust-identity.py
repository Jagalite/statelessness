from pathlib import Path
import json,re
r=Path(__file__).resolve().parent
receipt=json.loads((r/'final-installed/qualification.json').read_text())
root=Path(receipt['workspace'])/'rust-source'
files=[Path('Cargo.toml'),Path('build.rs'),Path('docs/requirements/controller-properties.txt'),Path('docs/requirements/controller.json')]
files += [p.relative_to(root) for p in (root/'src').rglob('*') if p.is_file()]
version=(r/'final-installed/logs/rustc-version.log').read_bytes()
target='aarch64-apple-darwin'
def calculate(order):
    value=0xcbf29ce484222325
    def add(data):
        nonlocal value
        for byte in len(data).to_bytes(8,'little')+data:
            value=((value^byte)*0x100000001b3)&((1<<64)-1)
    for p in order:
        add(p.as_posix().encode());add((root/p).read_bytes())
    add(version)
    for k,v in [('TARGET',target),('PROFILE','release'),('OPT_LEVEL','3'),('DEBUG','false'),('CARGO_ENCODED_RUSTFLAGS','')]:
        add(k.encode());add(v.encode())
    return f'source-fnv1a64:{value:016x}'
bytewise=sorted(files,key=lambda p:p.as_posix().encode())
components=sorted(files,key=lambda p:tuple(part.encode() for part in p.parts))
log=(r/'final-installed/logs/rust-install.log').read_text()
actual=re.findall(r'^\[statelessness [^\]]+\] cargo:rustc-env=STATELESS_BUILD_ID=(source-fnv1a64:[0-9a-f]{16})$',log,re.M)
report=dict(actual=actual,bytewise=calculate(bytewise),components=calculate(components),bytewise_order=list(map(str,bytewise)),component_order=list(map(str,components)),rustc_stdout=version.decode(),target=target)
(r/'rust-identity-diagnosis.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if not k.endswith('_order')},indent=2))
assert actual==[report['components']]
assert report['bytewise']!=report['components']
