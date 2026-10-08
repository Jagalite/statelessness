#!/usr/bin/env python3
"""Observe clean and unchanged rebuild costs in isolated local target directories."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
files=sorted([*root.glob('src/**/*.rs'),*root.glob('crates/**/*.rs'),*root.glob('crates/**/Cargo.toml'),root/'Cargo.toml',root/'build.rs'])
initial_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
results=[]
with tempfile.TemporaryDirectory(prefix='stateless-build-measure-') as directory:
    temp=Path(directory)
    for name,selection in [('core',['-p','statelessness','--lib']),('macros',['-p','macro-qualification','--lib'])]:
        target=temp/name
        env={**os.environ,'CARGO_TARGET_DIR':str(target),'CARGO_INCREMENTAL':'0'}
        command=['cargo','build','--locked','--offline',*selection]
        for mode in ['clean','unchanged']:
            start=time.monotonic()
            result=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True)
            elapsed=time.monotonic()-start
            if result.returncode:
                raise RuntimeError(result.stdout+result.stderr)
            sizes={str(p.relative_to(target)):p.stat().st_size for p in (target/'debug').glob('lib*') if p.is_file()}
            results.append({'case':name,'mode':mode,'command':command,'seconds':elapsed,'top_level_library_bytes':sizes})
args.output.parent.mkdir(parents=True,exist_ok=True)
files=sorted([*root.glob('src/**/*.rs'),*root.glob('crates/**/*.rs'),*root.glob('crates/**/Cargo.toml'),root/'Cargo.toml',root/'build.rs'])
hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
assert hashes==initial_hashes, 'Source changed during measurement; rerun on a stable snapshot'
args.output.write_text(json.dumps({'source_sha256':hashes,'compiler':subprocess.check_output(['rustc','--version'],text=True).strip(),'results':results},indent=2)+'\n')
print(args.output)
