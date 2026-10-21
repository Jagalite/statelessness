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
