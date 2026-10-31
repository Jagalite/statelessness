#!/bin/sh
set -eu
repo=$(pwd)
task_tmp=$(mktemp -d)
trap 'rm -rf "$task_tmp"' EXIT
mkdir -p "$task_tmp/module" "$task_tmp/consumer"
cp -R go/. "$task_tmp/module/"
artifact_dir="$repo/validation/native-parity/n12-go/telemetry-installed/$(basename "$task_tmp")"
mkdir -p "$artifact_dir"
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
