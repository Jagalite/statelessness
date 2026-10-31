package main

import(
 "encoding/json"
 "fmt"
 "os"
 "strconv"
 "sync/atomic"
 s "github.com/Jagalite/statelessness/go"
)
func must(err error){if err!=nil{panic(err)}}
func model(calls *int)s.Model[int,int,string]{return s.Model[int,int,string]{
 InitialState:func()(int,error){*calls++;return 0,nil},Inputs:func(int)(s.Iterator[int],error){*calls++;return s.Values([]int{0,1}),nil},EqualStates:func(a,b int)bool{*calls++;return a==b},
 Step:func(state,input int)(s.Transition[int,string],error){*calls++;if input!=0&&input!=1{return s.Transition[int,string]{},fmt.Errorf("expected zero or one")};if input==0{return s.Accept[int,string](state),nil};return s.Accept[int,string](state+input,"changed"),nil},
 CheckState:func(state int)([]s.Check,error){*calls++;if state>=3{return []s.Check{s.Failed("threshold.bound","at least three")},nil};return []s.Check{s.Passed("threshold.bound")},nil},
 EstimatedStateBytes:func(int)(*uint64,error){*calls++;value:=uint64(5);return &value,nil},CloneState:func(v int)int{*calls++;return v},CloneInput:func(v int)int{*calls++;return v},CloneOutput:func(v string)string{*calls++;return v},
 Codec:&s.Codec[int,int,string]{Metadata:func()(s.Metadata,error){return s.Identity("native-bfs-threshold","application-v1"),nil},
 EncodeState:func(v int)([]byte,error){return []byte(strconv.Itoa(v)),nil},DecodeState:func(v []byte)(int,error){return strconv.Atoi(string(v))},
 EncodeInput:func(v int)([]byte,error){return []byte(strconv.Itoa(v)),nil},DecodeInput:func(v []byte)(int,error){if string(v)!="0"&&string(v)!="1"{return 0,fmt.Errorf("expected zero or one")};return strconv.Atoi(string(v))},
 EncodeOutput:func(v string)([]byte,error){return []byte(v),nil}},
}}
func search(cap *uint64,cancelled bool)(map[string]any,*s.Failure[int]){
 calls,initials,errors:=0,0,0;histories:=[][]int{};cancel:=&atomic.Bool{};cancel.Store(cancelled)
 observer:=s.SearchObserver[int,int,string]{NeedsHistory:func()bool{return true},Initial:func(int,[]s.Check)error{initials++;return nil},
  Transition:func(_ int,_ int,_ s.Transition[int,string],_ []s.Check,_ []s.Check,history []int)error{histories=append(histories,append([]int{},history...));return nil},CheckError:func(string,[]s.Check,bool)error{errors++;return nil}}
 result,err:=s.EnumerateObserved(model(&calls),s.SearchConfig{MaxStates:100,MaxTransitions:100,MaxDepth:10},s.SearchLimits{Control:s.RunLimits{Cancellation:cancel},MaxEstimatedStateBytes:cap},observer);must(err)
 var witness,phase,property any;if result.Failure!=nil{witness=result.Failure.Inputs;phase=result.Failure.Violations[0].Phase;property=result.Failure.Violations[0].Check.ID}
 return map[string]any{"termination":result.Termination,"states":result.States,"transitions":result.Transitions,"estimated_retained_state_bytes":result.EstimatedRetainedStateBytes,
  "witness":witness,"failure_phase":phase,"failure_property":property,"observed_histories":histories,"initial_observations":initials,"check_errors":errors,
  "model_callbacks":calls,"observer_callbacks":initials+len(histories)+errors},result.Failure
}
func main(){
 if len(os.Args)!=3{panic("expected record|replay PATH")};mode,path:=os.Args[1],os.Args[2];var receipt map[string]any
 if mode=="record"{
  base,failure:=search(nil,false);capValue:=uint64(14);cap,_:=search(&capValue,false);precancel,_:=search(nil,true);if failure==nil{panic("expected BFS witness")}
  calls:=0;trace,err:=s.Record(model(&calls),s.Values(failure.Inputs),10);must(err);data,err:=s.MarshalTrace(trace);must(err);must(os.WriteFile(path,data,0600))
  receipt=map[string]any{"base":base,"cap":cap,"precancel":precancel}
 }else if mode=="replay"{
  data,err:=os.ReadFile(path);must(err);trace,err:=s.ParseTrace(data);must(err);calls:=0;result,err:=s.Replay(model(&calls),trace,false);must(err)
  receipt=map[string]any{"outcome":result.Outcome,"failure_reproduced":result.FailureReproduced,"steps":len(trace.Steps)}
 }else{panic("expected record or replay")};must(json.NewEncoder(os.Stdout).Encode(receipt))
}
