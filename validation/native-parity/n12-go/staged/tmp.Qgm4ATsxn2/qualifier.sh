#!/bin/sh
set -eu
repo=$(pwd)
task_tmp=$(mktemp -d)
artifact_dir="$repo/validation/native-parity/n12-go/staged/$(basename "$task_tmp")"
mkdir -p "$artifact_dir" "$task_tmp/consumer"
cp "$repo/scripts/native-parity/qualify-n12-go.sh" "$artifact_dir/qualifier.sh"
shasum -a 256 "$artifact_dir/qualifier.sh"
# Tests and installed consumer use one frozen source snapshot, including corpora.
manifest="$repo/scripts/native-parity/qualify-n12-go-manifest.py"
python3 "$manifest" "$repo" "$artifact_dir/live-source-before.json" go conformance scripts/native-parity/qualify-n12-go.sh scripts/native-parity/qualify-n12-go-telemetry-installed.sh scripts/native-parity/qualify-n12-go-manifest.py
finish() {
 result=$?
 trap - EXIT
 cd "$repo"
 python3 "$manifest" "$repo" "$artifact_dir/live-source-after.json" go conformance scripts/native-parity/qualify-n12-go.sh scripts/native-parity/qualify-n12-go-telemetry-installed.sh scripts/native-parity/qualify-n12-go-manifest.py || result=1
 cmp "$artifact_dir/live-source-before.json" "$artifact_dir/live-source-after.json" || result=1
 rm -rf "$task_tmp"
 exit "$result"
}
trap finish EXIT
cp -R go "$task_tmp/go"
cp -R conformance "$task_tmp/conformance"
python3 "$manifest" "$task_tmp" "$artifact_dir/copied-source-identity.json" go conformance
python3 - "$artifact_dir/live-source-before.json" "$artifact_dir/copied-source-identity.json" full <<'PYCHECK'
import json,sys
live=json.load(open(sys.argv[1])); copied=json.load(open(sys.argv[2])); telemetry=sys.argv[3]=='telemetry'
expected={k:v for k,v in live.items() if k=='go' or k.startswith('go/') or (not telemetry and (k=='conformance' or k.startswith('conformance/')))}
if telemetry: copied={('go'+k[len('module'):]):v for k,v in copied.items()}
if expected!=copied: raise RuntimeError('copied source membership/type/hash differs from live source before copy')
print('copied source identity matches captured live source')
PYCHECK
go version
go env GOOS GOARCH GOVERSION CGO_ENABLED GOWORK
python3 - "$task_tmp" "$artifact_dir/source-manifest.json" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);rows={}
for directory in ('go','conformance'):
 for p in sorted((root/directory).rglob('*')):
  if p.is_file(): rows[str(p.relative_to(root))]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
data=(json.dumps(rows,sort_keys=True,indent=2)+'\n').encode();pathlib.Path(sys.argv[2]).write_bytes(data)
print('staged source manifest:',sys.argv[2],'sha256',hashlib.sha256(data).hexdigest(),'files',len(rows))
PY
tar -czf "$artifact_dir/source.tar.gz" -C "$task_tmp" go conformance
cd "$task_tmp/go"
go test -count=1 ./...
go vet ./...
cat > "$task_tmp/consumer/go.mod" <<MOD
module debuggerconsumer

go 1.23.0
require github.com/Jagalite/statelessness/go v0.0.0
replace github.com/Jagalite/statelessness/go => $task_tmp/go
MOD
cat > "$task_tmp/consumer/main.go" <<'GO'
package main
import (
 "fmt"
 "strconv"
 s "github.com/Jagalite/statelessness/go"
 d "github.com/Jagalite/statelessness/go/debug"
)
func require(ok bool){if !ok{panic("installed consumer assertion")}}
func main(){
 encode:=func(v int)([]byte,error){return []byte(strconv.Itoa(v)),nil};decode:=func(v []byte)(int,error){return strconv.Atoi(string(v))}
 checks:=func(v int)([]s.Check,error){if v>=12{return []s.Check{s.Failed("safe","limit")},nil};return []s.Check{s.Passed("safe")},nil}
 model:=s.Model[int,int,int]{InitialState:func()(int,error){panic("unexpected initializer")},CloneState:s.ValueCopy[int],CloneInput:s.ValueCopy[int],CloneOutput:s.ValueCopy[int],Step:func(x,i int)(s.Transition[int,int],error){return s.Accept(x+i,x+i),nil},CheckState:checks,Codec:&s.Codec[int,int,int]{Metadata:func()(s.Metadata,error){return s.Identity("installed","manual"),nil},EncodeState:encode,DecodeState:decode,EncodeInput:encode,DecodeInput:decode,EncodeOutput:encode}}
 equal:=func(a,b int)(bool,error){return a==b,nil}
 a:=s.Artifact{V2:&s.TraceV2{Trace:s.AuditTrace{Trace:s.Trace{Metadata:s.Identity("installed","manual"),InitialState:[]byte("10"),InitialChecks:[]s.Check{s.Passed("safe")},Steps:[]s.TraceStep{{Input:[]byte("2"),PostState:[]byte("12"),Disposition:s.Accepted(),Outputs:[][]byte{[]byte("12")},Checks:[]s.Check{s.Failed("safe","limit")}}},Termination:"property_failed"}},CheckpointAfter:9007199254740993,Provenance:s.Provenance{Run:"installed",Epoch:18446744073709551615},Steps:[]s.StepEvidence{{Sequence:9007199254740994,StateCheckCount:1}}}}
 viewer,e:=d.NewEvidenceViewer(a,s.DefaultEvidenceLimits());if e!=nil{panic(e)};b,ok:=viewer.Boundary(1);require(ok&&b.HostSequence==9007199254740994&&string(b.Before)=="10");failure,e:=viewer.Failure(1,0);require(e==nil&&failure.Phase==s.PhaseState)
 maximum:=uint64(20);report,e:=d.Minimize(viewer,s.GenerateModel[int,int,int]{Model:model,InputEqual:equal},1,0,s.DefaultShrinkConfig(),s.ShrinkLimits{MaxReplayedTransitions:&maximum});if e!=nil{panic(e)};require(report.ValidatedOriginal&&len(report.Minimized.Inputs)==1&&report.Minimized.Inputs[0]==2&&report.ReplayedTransitions>=3)
 session,e:=d.SessionFromState("installed",model,10,d.Unrestricted[int,int](),equal,d.DefaultSessionLimits());if e!=nil{panic(e)};turn,e:=session.Step(0,1);require(e==nil&&turn.Delivered&&session.State()==11&&session.Revision()==1)
 branch,e:=d.Fork(viewer,model,0,"child",d.Unrestricted[int,int](),equal,d.DefaultSessionLimits(),s.ShrinkLimits{MaxReplayedTransitions:&maximum});if e!=nil{panic(e)};require(branch.Provenance.ParentPrefixVerified);require(branch.Provenance.ValidateParent(a.ExactTrace(),s.DefaultBinaryLimits())==nil);trace,e:=branch.ExportTrace();require(e==nil&&trace.Config.Strategy=="debugger-branch")
 fmt.Println("fresh installed snapshot consumer: v2 full-width identities, checkpoint/phase, exact minimization, typed session and verified recorded fork passed")
}
GO
cd "$task_tmp/consumer"
GOWORK=off go build -o consumer .
./consumer
cp main.go go.mod "$artifact_dir/"
gzip -c consumer > "$artifact_dir/consumer.gz"
shasum -a 256 consumer
python3 - "$artifact_dir" <<'PY'
import hashlib,pathlib,sys
for p in sorted(pathlib.Path(sys.argv[1]).iterdir()):
 if p.is_file():print('artifact',p.name,'bytes',p.stat().st_size,'sha256',hashlib.sha256(p.read_bytes()).hexdigest())
PY

