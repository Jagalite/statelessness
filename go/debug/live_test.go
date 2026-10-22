package debug_test

import (
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"math"
	"testing"
)

func controller(t *testing.T) *d.LiveController[int] {
	t.Helper()
	o := d.DefaultControllerOptions()
	o.Authority = d.ControlAuthority{PauseAndStep: true, Inject: true}
	c, e := d.NewLiveController[int](7, 9007199254740993, o)
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func TestLiveCooperativePermitsAndReconnect(t *testing.T) {
	c := controller(t)
	r := c.Status().Revision
	if e := c.ExternalArrival(9, 4); e != nil {
		t.Fatal(e)
	}
	if e := c.RequestPause(7, r); e != nil {
		t.Fatal(e)
	}
	if c.Status().Phase != d.LivePauseRequested {
		t.Fatal("pause acknowledged before safe point")
	}
	ack, e := c.SafePoint()
	if e != nil || ack.Revision != r {
		t.Fatal(ack, e)
	}
	if e = c.AuthorizeOne(7, r); e != nil {
		t.Fatal(e)
	}
	p, i, ok, e := c.BeginNext()
	if e != nil || !ok || i != 9 {
		t.Fatal(i, ok, e)
	}
	other := controller(t)
	if e = other.Delivered(p, 1); e == nil {
		t.Fatal("foreign permit")
	}
	if e = c.Delivered(p, 1); e != nil {
		t.Fatal(e)
	}
	if e = c.Delivered(p, 1); e == nil {
		t.Fatal("duplicate permit")
	}
	a, e := c.SafePoint()
	if e != nil || a.EffectPhase != d.EffectsStaged {
		t.Fatal(a, e)
	}
	if e = c.Resume(7, r+1); e != nil {
		t.Fatal(e)
	}
	dp, e := c.TakeDispatch(0)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.SafePoint(); e == nil {
		t.Fatal("inflight safepoint")
	}
	c.Disconnect()
	if _, e = c.Reconnect(); e == nil {
		t.Fatal("inflight reconnect")
	}
	if e = c.Dispatched(dp); e != nil {
		t.Fatal(e)
	}
	epoch, e := c.Reconnect()
	if e != nil || epoch != 8 || dp.Origin().Epoch != 7 {
		t.Fatal(epoch, e)
	}
	if e = c.Resume(7, r+1); e == nil {
		t.Fatal("old epoch")
	}
	if e = c.Dispatched(dp); e == nil {
		t.Fatal("dispatch retried")
	}
}
func TestLiveBoundsAndInFlightDetach(t *testing.T) {
	o := d.DefaultControllerOptions()
	o.MaxArrivals = 1
	o.MaxArrivalBytes = 1
	o.Detach = d.TerminateOnDetach
	c, _ := d.NewLiveController[int](1, 0, o)
	if e := c.ExternalArrival(1, 1); e != nil {
		t.Fatal(e)
	}
	if e := c.ExternalArrival(2, 1); e == nil || c.Status().BufferedArrivals != 1 {
		t.Fatal(e)
	}
	p, _, _, _ := c.BeginNext()
	c.Disconnect()
	if c.Status().Phase != d.LiveDelivering {
		t.Fatal("discarded admitted turn")
	}
	if e := c.Delivered(p, 0); e != nil || c.Status().Revision != 1 || c.Status().Phase != d.LiveTerminated {
		t.Fatal(e, c.Status())
	}
	c, _ = d.NewLiveController[int](1, math.MaxUint64, o)
	c.ExternalArrival(1, 1)
	if _, _, _, e := c.BeginNext(); e == nil || c.Status().BufferedArrivals != 1 {
		t.Fatal("revision exhaustion consumed input")
	}
}
func TestLiveBridgeUsesCheckedTokensWithoutExecuting(t *testing.T) {
	calls := 0
	m := model(&calls).Model
	initial, e := s.CheckInitial(m, 10)
	if e != nil {
		t.Fatal(e)
	}
	limits := d.DefaultLiveLimits()
	limits.MaxEvents = 1
	bridge, e := d.AttachLiveBridge[int, int, int](4, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), limits)
	if e != nil {
		t.Fatal(e)
	}
	turn, e := s.CheckTurn(m, 10, 1, s.Accept(11, 11), 1, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if e = bridge.Observe(4, 1, turn); e != nil {
		t.Fatal(e)
	}
	if calls != 0 {
		t.Fatal("bridge reexecuted reducer")
	}
	if e = bridge.EffectDispatched(d.EffectOrigin{Epoch: 4, Transition: 1, OutputIndex: 0}); e != nil {
		t.Fatal(e)
	}
	if e = bridge.EffectDispatched(d.EffectOrigin{Epoch: 4, Transition: 1, OutputIndex: 0}); e == nil {
		t.Fatal("duplicate dispatch")
	}
	h := bridge.Health()
	if h.RecordedSteps != 1 || h.EventDrops != 2 || !h.CaptureComplete {
		t.Fatal(h)
	}
	if len(bridge.Export().Steps) != 1 {
		t.Fatal("no exact capture")
	}
	if e = bridge.Observe(4, 1, turn); e == nil || bridge.Health().DuplicateObservations != 1 {
		t.Fatal("duplicate turn")
	}
}
func TestDispatchFailureCopiedPermitCannotConfirm(t *testing.T) {
	c := controller(t)
	c.ExternalArrival(1, 1)
	p, _, _, _ := c.BeginNext()
	c.Delivered(p, 1)
	dp, e := c.TakeDispatch(0)
	if e != nil {
		t.Fatal(e)
	}
	if e = c.DispatchFailed(dp); e != nil {
		t.Fatal(e)
	}
	if e = c.Dispatched(dp); e == nil {
		t.Fatal("uncertain dispatch later confirmed")
	}
	if _, _, _, e = c.BeginNext(); e == nil {
		t.Fatal("uncertain dispatch retried")
	}
}
func TestLiveBridgeGapAndIdentityNeverFabricateExactPrefix(t *testing.T) {
	calls := 0
	m := model(&calls).Model
	initial, e := s.CheckInitial(m, 10)
	if e != nil {
		t.Fatal(e)
	}
	bridge, e := d.AttachLiveBridge[int, int, int](1, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), d.DefaultLiveLimits())
	if e != nil {
		t.Fatal(e)
	}
	copyModel := m
	codec := *m.Codec
	codec.Metadata = func() (s.Metadata, error) { return s.Identity("different", "manual"), nil }
	copyModel.Codec = &codec
	turn, e := s.CheckTurn(copyModel, 10, 1, s.Accept(11, 11), 1, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if e = bridge.Observe(1, 1, turn); e == nil || bridge.Health().HostSequence != 0 || bridge.Health().CaptureComplete {
		t.Fatal("identity changed exact capture", e, bridge.Health())
	}
	bridge, e = d.AttachLiveBridge[int, int, int](1, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), d.DefaultLiveLimits())
	if e != nil {
		t.Fatal(e)
	}
	turn, e = s.CheckTurn(m, 10, 1, s.Accept(11, 11), 3, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if e = bridge.Observe(1, 3, turn); e == nil || bridge.Health().HostSequence != 3 || bridge.Health().CaptureComplete || len(bridge.Export().Steps) != 0 || calls != 0 {
		t.Fatal("gap fabricated exact turn", e, bridge.Health())
	}
}
