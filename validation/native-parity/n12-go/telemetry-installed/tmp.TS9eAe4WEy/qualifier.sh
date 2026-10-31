#!/bin/sh
set -eu
repo=$(pwd)
task_tmp=$(mktemp -d)
mkdir -p "$task_tmp/module" "$task_tmp/consumer"
artifact_dir="$repo/validation/native-parity/n12-go/telemetry-installed/$(basename "$task_tmp")"
mkdir -p "$artifact_dir"
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
cp -R go/. "$task_tmp/module/"
python3 "$manifest" "$task_tmp" "$artifact_dir/copied-source-identity.json" module
python3 - "$artifact_dir/live-source-before.json" "$artifact_dir/copied-source-identity.json" telemetry <<'PYCHECK'
import json,sys
live=json.load(open(sys.argv[1])); copied=json.load(open(sys.argv[2])); telemetry=sys.argv[3]=='telemetry'
expected={k:v for k,v in live.items() if k=='go' or k.startswith('go/') or (not telemetry and (k=='conformance' or k.startswith('conformance/')))}
if telemetry: copied={('go'+k[len('module'):]):v for k,v in copied.items()}
if expected!=copied: raise RuntimeError('copied source membership/type/hash differs from live source before copy')
print('copied source identity matches captured live source')
PYCHECK
cp "$repo/scripts/native-parity/qualify-n12-go-telemetry-installed.sh" "$artifact_dir/qualifier.sh"
python3 - "$task_tmp/module" "$artifact_dir/source-manifest.json" <<'PYHASH'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);rows={str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(root.rglob('*')) if p.is_file()}
data=(json.dumps(rows,sort_keys=True,indent=2)+'\n').encode();pathlib.Path(sys.argv[2]).write_bytes(data)
print('frozen copied-module source manifest',sys.argv[2],hashlib.sha256(data).hexdigest(),len(rows))
PYHASH
tar -czf "$artifact_dir/source.tar.gz" -C "$task_tmp" module
go version
cat > "$task_tmp/consumer/go.mod" <<MOD
module telemetryconsumer

go 1.23.0
require github.com/Jagalite/statelessness/go v0.0.0
replace github.com/Jagalite/statelessness/go => $task_tmp/module
MOD
cat > "$task_tmp/consumer/main.go" <<'GO'
package main
import("fmt";"math";d "github.com/Jagalite/statelessness/go/debug")
func main(){m,e:=d.NewMetricsStore(math.MaxUint64,d.DefaultLabelCatalog(),d.DefaultHistogramSchema(),d.DefaultMetricLimits());if e!=nil{panic(e)};l:=d.Labels{Origin:d.OriginLive};m.Observe(d.QueueWait,l,math.MaxUint64);s,e:=m.Snapshot(nil);if e!=nil{panic(e)};view:=d.DashboardFromSnapshot(s,d.TelemetryHealth{},[]d.Labels{l},100);b,e:=view.JSON(1<<20);if e!=nil||len(b)==0{panic("report")};o:=d.DefaultControllerOptions();o.Authority.PauseAndStep=true;c,e:=d.NewLiveController[int](1,9007199254740993,o);if e!=nil{panic(e)};if e=c.RequestPause(1,9007199254740993);e!=nil{panic(e)};ack,e:=c.SafePoint();if e!=nil||ack.Revision!=9007199254740993{panic("revision")};fmt.Println("fresh copied-module process: full-width metrics/report + cooperative gate passed")}
GO
cd "$task_tmp/consumer"
cp main.go "$artifact_dir/consumer.go"
GOWORK=off go build -o "$task_tmp/telemetry-consumer" .
"$task_tmp/telemetry-consumer"
shasum -a 256 "$task_tmp/telemetry-consumer"
gzip -c "$task_tmp/telemetry-consumer" > "$artifact_dir/consumer-binary.gz"
