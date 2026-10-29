import Statelessness
import Foundation
if CommandLine.arguments.count>1 { try networkMain(); exit(0) }
let m:LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(limits:RetentionLimits(obligations:2,resources:2,violations:8),cloneKey:{$0})
let key = ScopedKey(epoch:2,generation:3,key:UInt8(7))
let c = try m.historyCodec(key:scopedKeyCodec(ValueCodecs.u8))
var h = try m.advance(LifecycleHistory(),events:[.begin(key,deadline:10),.settle(key),.fence(before:3)])
h = try c.fromTrace(c.traceBytes(h,maximum:1024)); h = try m.advance(h,events:[.settle(key)])
guard h.violations.first?.code == "retired_epoch_reference" else { fatalError("stale reference accepted") }
print(try c.traceBytes(h,maximum:1024).map { let h = String($0,radix:16); return h.count == 1 ? "0"+h : h }.joined())
