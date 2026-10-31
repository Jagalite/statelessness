package main

import (
 "encoding/json"
 "fmt"
 "os"
 "strconv"
 s "github.com/Jagalite/statelessness/go"
)

func must(err error) { if err != nil { panic(err) } }
func main() {
 if len(os.Args) != 3 { panic("usage: consumer record|replay PATH") }
 mode, path := os.Args[1], os.Args[2]
 model := s.Model[int,int,string]{
  InitialState: func()(int,error) { return 0,nil },
  Step: func(state,input int)(s.Transition[int,string],error) { if input != 0 && input != 1 { return s.Transition[int,string]{},fmt.Errorf("expected zero or one") }; if input==0 { return s.Accept[int,string](state),nil }; return s.Accept[int,string](state+input,"changed"),nil },
  CheckState: func(state int)([]s.Check,error) { if state >= 3 { return []s.Check{s.Failed("threshold.bound","at least three")},nil }; return []s.Check{s.Passed("threshold.bound")},nil },
  CloneState:s.ValueCopy[int], CloneInput:s.ValueCopy[int], CloneOutput:s.ValueCopy[string],
  Codec:&s.Codec[int,int,string]{Metadata:func()(s.Metadata,error) { m:=s.Identity("native-search-threshold","application-v2"); m.ModelVersion=2; return m,nil },
   EncodeState:func(v int)([]byte,error) { return []byte(strconv.Itoa(v)),nil }, DecodeState:func(v []byte)(int,error) { return strconv.Atoi(string(v)) },
   EncodeInput:func(v int)([]byte,error) { return []byte(strconv.Itoa(v)),nil }, DecodeInput:func(v []byte)(int,error) { if string(v)!="0" && string(v)!="1" { return 0,fmt.Errorf("expected zero or one") }; return strconv.Atoi(string(v)) },
   EncodeOutput:func(v string)([]byte,error) { return []byte(v),nil }},
 }
 generated := s.GenerateModel[int,int,string]{Model:model, Generate:func(_ int,rng *s.Rng)(int,bool,error) { index,_:=rng.Index(2); return 1-int(index),true,nil },
  IsEnabled:func(_ int,i int)(bool,error) { return i==0||i==1,nil }, InputEqual:func(a,b int)(bool,error) { return a==b,nil }}
 searchReceipt:=map[string]any{}
 if mode == "record" {
  found,err:=s.Fuzz(generated,s.FuzzConfig{Seed:0,Cases:4,MaxSteps:32,MaxTransitions:128,MutationPercent:0}); must(err)
  if found.Termination!="failure_found" || found.Failure==nil { panic("failure missing") }
  originalTrace,err:=s.Record(model,s.Values(found.Failure.Inputs),32); must(err)
  originalData,err:=s.MarshalTrace(originalTrace); must(err); must(os.WriteFile(path+".original.json",originalData,0600))
  reduced,err:=s.Shrink(generated,*found.Failure,s.DefaultShrinkConfig()); must(err)
  target:=func(f s.Failure[int]) bool { return len(f.Violations)==1 && f.Violations[0].Phase=="state" && f.Violations[0].Check.ID=="threshold.bound" }
  if !reduced.ValidatedOriginal || reduced.Termination!="search_complete" || len(reduced.Minimized.Inputs)!=3 || len(found.Failure.Inputs)<=3 || reduced.Attempts<=0 || !target(*found.Failure)||!target(reduced.Minimized) { panic("shrink incomplete") }
  for _,input:=range reduced.Minimized.Inputs { if input!=1 { panic("shrunk witness contains a no-op") } }
  searchReceipt=map[string]any{"original_steps":len(found.Failure.Inputs),"minimized_steps":len(reduced.Minimized.Inputs),"shrink_attempts":reduced.Attempts,"validated_original":reduced.ValidatedOriginal,"phase":"state","property":"threshold.bound"}
  trace,err:=s.Record(model,s.Values(reduced.Minimized.Inputs),10); must(err)
  data,err:=s.MarshalTrace(trace); must(err); must(os.WriteFile(path,data,0600))
 } else if mode!="replay" { panic("expected record or replay") }
 data,err:=os.ReadFile(path); must(err); trace,err:=s.ParseTrace(data); must(err)
 result,err:=s.Replay(model,trace,false); must(err)
 if result.Outcome!="exact" || !result.FailureReproduced { panic("failure did not replay") }
 searchReceipt["mode"]=mode; searchReceipt["outcome"]=result.Outcome; searchReceipt["failure_reproduced"]=result.FailureReproduced; searchReceipt["steps"]=len(trace.Steps)
 must(json.NewEncoder(os.Stdout).Encode(searchReceipt))
}
