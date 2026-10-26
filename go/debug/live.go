package debug

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"strconv"
)

var ErrLivePhase = errors.New("live: wrong phase or permit")
var ErrLiveAuthority = errors.New("live: stale epoch/revision or unauthorized")
var ErrLiveLimit = errors.New("live: exhausted or bounded capacity")

type ControllerPhase string

const (
	LiveRunning        ControllerPhase = "running"
	LivePauseRequested ControllerPhase = "pause-requested"
	LivePaused         ControllerPhase = "paused"
	LiveOneTurn        ControllerPhase = "one-turn-authorized"
	LiveDelivering     ControllerPhase = "delivering"
	LiveTerminated     ControllerPhase = "terminated"
)

type EffectPhase string

const (
	EffectsDispatched  EffectPhase = "dispatched"
	EffectsStaged      EffectPhase = "staged"
	EffectsDispatching EffectPhase = "dispatching"
)

type DetachPolicy string

const (
	RemainPaused      DetachPolicy = "remain-paused"
	ResumeOnDetach    DetachPolicy = "resume"
	TerminateOnDetach DetachPolicy = "terminate"
)

type OverflowPolicy string

const (
	Backpressure         OverflowPolicy = "backpressure"
	DisconnectOnOverflow OverflowPolicy = "disconnect"
)

type ControlAuthority struct{ PauseAndStep, Inject bool }
type ControllerOptions struct {
	Authority                                ControlAuthority
	Detach                                   DetachPolicy
	Overflow                                 OverflowPolicy
	MaxArrivals, MaxArrivalBytes, MaxOutputs int
}

func DefaultControllerOptions() ControllerOptions {
	return ControllerOptions{Detach: RemainPaused, Overflow: Backpressure, MaxArrivals: 1024, MaxArrivalBytes: 1 << 20, MaxOutputs: 4096}
}

type EffectOrigin struct {
	Epoch, Transition uint64
	OutputIndex       int
}
type PauseAck struct {
	Generation, Epoch, Revision uint64
	EffectPhase                 EffectPhase
}
type permitOwner struct{ marker byte }
type DeliveryPermit struct {
	owner                   *permitOwner
	epoch, revision, serial uint64
}
type DispatchPermit struct {
	owner           *permitOwner
	epoch, revision uint64
	output          int
}

func (p DispatchPermit) Origin() EffectOrigin { return EffectOrigin{p.epoch, p.revision, p.output} }

type ControllerStatus struct {
	Epoch, OriginEpoch, Revision    uint64
	Phase                           ControllerPhase
	Connected                       bool
	EffectPhase                     EffectPhase
	BufferedArrivals, BufferedBytes int
	OverflowCount, PauseGeneration  uint64
}
type arrival[I any] struct {
	input I
	bytes int
}

// LiveController is a single-owner cooperative gate. It executes no callback,
// reads no clock, and cannot preempt host work. Copying a permit grants no retry.
type LiveController[I any] struct {
	options                                                                 ControllerOptions
	owner                                                                   *permitOwner
	epoch, originEpoch, revision, serial, active, pauseGeneration, overflow uint64
	phase                                                                   ControllerPhase
	connected, repause, terminatePending                                    bool
	effects                                                                 EffectPhase
	staged                                                                  []byte
	arrivals                                                                []arrival[I]
	bytes, inflight, remaining                                              int
}

func NewLiveController[I any](epoch, revision uint64, o ControllerOptions) (*LiveController[I], error) {
	if epoch == 0 || o.MaxArrivals <= 0 || o.MaxArrivalBytes <= 0 || o.MaxOutputs <= 0 || (o.Detach != RemainPaused && o.Detach != ResumeOnDetach && o.Detach != TerminateOnDetach) || (o.Overflow != Backpressure && o.Overflow != DisconnectOnOverflow) {
		return nil, ErrLiveLimit
	}
	return &LiveController[I]{options: o, owner: &permitOwner{}, epoch: epoch, originEpoch: epoch, revision: revision, phase: LiveRunning, connected: true, effects: EffectsDispatched}, nil
}
func (c *LiveController[I]) Status() ControllerStatus {
	return ControllerStatus{c.epoch, c.originEpoch, c.revision, c.phase, c.connected, c.effects, len(c.arrivals), c.bytes, c.overflow, c.pauseGeneration}
}
func (c *LiveController[I]) Options() ControllerOptions { return c.options }
func (c *LiveController[I]) command(e, r uint64, allowed bool) error {
	if !c.connected || e != c.epoch || r != c.revision || !allowed {
		return ErrLiveAuthority
	}
	return nil
}
func (c *LiveController[I]) RequestPause(e, r uint64) error {
	if x := c.command(e, r, c.options.Authority.PauseAndStep); x != nil {
		return x
	}
	switch c.phase {
	case LiveRunning, LiveOneTurn:
		c.phase = LivePauseRequested
	case LiveDelivering:
		c.repause = true
	case LivePaused, LivePauseRequested:
	default:
		return ErrLivePhase
	}
	return nil
}
func (c *LiveController[I]) SafePoint() (*PauseAck, error) {
	if c.inflight != 0 {
		return nil, ErrLivePhase
	}
	switch c.phase {
	case LivePauseRequested:
		if c.pauseGeneration == math.MaxUint64 {
			return nil, ErrLiveLimit
		}
		c.pauseGeneration++
		c.phase = LivePaused
	case LivePaused:
	case LiveRunning, LiveOneTurn:
		return nil, nil
	default:
		return nil, ErrLivePhase
	}
	return &PauseAck{c.pauseGeneration, c.epoch, c.revision, c.effects}, nil
}
func (c *LiveController[I]) AuthorizeOne(e, r uint64) error {
	if x := c.command(e, r, c.options.Authority.PauseAndStep); x != nil {
		return x
	}
	if c.phase != LivePaused {
		return ErrLivePhase
	}
	c.phase = LiveOneTurn
	return nil
}
func (c *LiveController[I]) Resume(e, r uint64) error {
	if x := c.command(e, r, c.options.Authority.PauseAndStep); x != nil {
		return x
	}
	if c.phase != LivePaused && c.phase != LivePauseRequested && c.phase != LiveOneTurn {
		return ErrLivePhase
	}
	c.phase = LiveRunning
	return nil
}

// Rejection leaves ownership with the caller; accepted payload bytes are a host estimate.
func (c *LiveController[I]) ExternalArrival(input I, ownedBytes int) error {
	if c.phase == LiveTerminated {
		return ErrLivePhase
	}
	if ownedBytes < 0 {
		return ErrLiveLimit
	}
	if len(c.arrivals) >= c.options.MaxArrivals || ownedBytes > c.options.MaxArrivalBytes-c.bytes {
		if c.overflow < math.MaxUint64 {
			c.overflow++
		}
		if c.options.Overflow == DisconnectOnOverflow {
			c.Disconnect()
		}
		return ErrLiveLimit
	}
	c.arrivals = append(c.arrivals, arrival[I]{input, ownedBytes})
	c.bytes += ownedBytes
	return nil
}
func (c *LiveController[I]) Inject(e, r uint64, input I, n int) error {
	if x := c.command(e, r, c.options.Authority.Inject); x != nil {
		return x
	}
	return c.ExternalArrival(input, n)
}
func (c *LiveController[I]) BeginNext() (DeliveryPermit, I, bool, error) {
	var zero I
	if (c.phase != LiveRunning && c.phase != LiveOneTurn) || c.effects != EffectsDispatched {
		return DeliveryPermit{}, zero, false, ErrLivePhase
	}
	if len(c.arrivals) == 0 {
		return DeliveryPermit{}, zero, false, nil
	}
	if c.serial == math.MaxUint64 || c.revision == math.MaxUint64 {
		return DeliveryPermit{}, zero, false, ErrLiveLimit
	}
	a := c.arrivals[0]
	c.arrivals[0] = arrival[I]{}
	c.arrivals = c.arrivals[1:]
	c.bytes -= a.bytes
	c.repause = c.phase == LiveOneTurn
	c.phase = LiveDelivering
	c.serial++
	c.active = c.serial
	return DeliveryPermit{c.owner, c.epoch, c.revision, c.serial}, a.input, true, nil
}
func (c *LiveController[I]) deliveryValid(p DeliveryPermit) bool {
	return p.owner == c.owner && c.phase == LiveDelivering && p.epoch == c.epoch && p.revision == c.revision && p.serial == c.active && c.active != 0
}
func (c *LiveController[I]) Delivered(p DeliveryPermit, outputs int) error {
	if !c.deliveryValid(p) {
		return ErrLivePhase
	}
	if outputs < 0 {
		return ErrLiveLimit
	}
	c.active = 0
	c.revision++
	c.remaining = outputs
	c.inflight = 0
	c.effects = EffectsDispatched
	if outputs > 0 {
		c.effects = EffectsStaged
	}
	if outputs > c.options.MaxOutputs {
		c.phase = LiveTerminated
		return ErrLiveLimit
	}
	c.staged = make([]byte, outputs)
	c.phase = LiveRunning
	if c.repause {
		c.phase = LivePauseRequested
	}
	if c.terminatePending {
		c.phase = LiveTerminated
	}
	c.repause = false
	return nil
}
func (c *LiveController[I]) DeliveryFailed(p DeliveryPermit) error {
	if !c.deliveryValid(p) {
		return ErrLivePhase
	}
	c.active = 0
	c.phase = LiveTerminated
	return nil
}
func (c *LiveController[I]) TakeDispatch(index int) (DispatchPermit, error) {
	if c.phase != LiveRunning && c.phase != LivePauseRequested && c.phase != LiveOneTurn {
		return DispatchPermit{}, ErrLivePhase
	}
	if index < 0 || index >= len(c.staged) || c.staged[index] != 0 {
		return DispatchPermit{}, ErrLivePhase
	}
	c.staged[index] = 1
	c.inflight++
	c.effects = EffectsDispatching
	return DispatchPermit{c.owner, c.originEpoch, c.revision, index}, nil
}
func (c *LiveController[I]) dispatchValid(p DispatchPermit) bool {
	return p.owner == c.owner && p.epoch == c.originEpoch && p.revision == c.revision && p.output >= 0 && p.output < len(c.staged) && c.staged[p.output] == 1
}
func (c *LiveController[I]) Dispatched(p DispatchPermit) error {
	if !c.dispatchValid(p) {
		return ErrLivePhase
	}
	c.staged[p.output] = 2
	c.inflight--
	c.remaining--
	c.effects = EffectsStaged
	if c.remaining == 0 {
		c.effects = EffectsDispatched
	} else if c.inflight > 0 {
		c.effects = EffectsDispatching
	}
	return nil
}
func (c *LiveController[I]) DispatchFailed(p DispatchPermit) error {
	if !c.dispatchValid(p) {
		return ErrLivePhase
	}
	c.staged[p.output] = 3 // uncertain: copied permit cannot later confirm
	c.phase = LiveTerminated
	return nil
}
func (c *LiveController[I]) Terminate() {
	c.terminatePending = true
	if c.phase != LiveDelivering {
		c.phase = LiveTerminated
	}
}
func (c *LiveController[I]) Disconnect() {
	if !c.connected {
		return
	}
	c.connected = false
	switch c.options.Detach {
	case TerminateOnDetach:
		c.Terminate()
	case ResumeOnDetach:
		if c.phase == LiveDelivering {
			c.repause = false
		} else if c.phase != LiveTerminated {
			c.phase = LiveRunning
		}
	default:
		if c.phase == LiveDelivering {
			c.repause = true
		} else if c.phase != LiveTerminated && c.phase != LivePaused {
			c.phase = LivePauseRequested
		}
	}
}
func (c *LiveController[I]) Reconnect() (uint64, error) {
	if c.connected || c.inflight != 0 || c.phase == LiveDelivering || c.phase == LiveTerminated {
		return 0, ErrLivePhase
	}
	if c.epoch == math.MaxUint64 {
		return 0, ErrLiveLimit
	}
	c.epoch++
	c.connected = true
	return c.epoch, nil
}

// LiveBridge accepts sealed checked tokens from real host turns. Detached
// observations are deliberately insufficient recording authority.
type LiveLimits struct{ MaxEvents, MaxEventBytes, MaxEffectOrigins int }

func DefaultLiveLimits() LiveLimits { return LiveLimits{1024, 1 << 20, 4096} }

type LiveEvent struct {
	Epoch, StreamSequence, HostSequence uint64
	Kind                                string
	Outputs                             int
	FullChecking                        bool
	Disposition                         string
	FailedChecks, ChecksOmitted         int
}
type LiveHealth struct {
	Epoch, AttachedAfter, HostSequence                               uint64
	CaptureComplete, RecorderFrozen                                  bool
	RecordedSteps                                                    uint64
	RetainedSteps                                                    int
	EvictedSteps, EventDrops, OriginEvictions, DuplicateObservations uint64
}
type bridgeOrigin struct {
	origin     EffectOrigin
	dispatched bool
}
type LiveBridge[S, I, O any] struct {
	epoch, attached, host, stream, drops, evictions, duplicates uint64
	metadata                                                    s.Metadata
	maxChecks                                                   uint64
	complete                                                    bool
	limits                                                      LiveLimits
	recorder                                                    *s.Recorder
	events                                                      []LiveEvent
	origins                                                     []bridgeOrigin
}

func AttachLiveBridge[S, I, O any](epoch, after uint64, initial *s.CheckedInitial[S, I, O], config s.RunConfig, record s.RecorderOptions, limits LiveLimits) (*LiveBridge[S, I, O], error) {
	if epoch == 0 || limits.MaxEvents <= 0 || limits.MaxEventBytes <= 0 || limits.MaxEffectOrigins <= 0 {
		return nil, ErrLiveLimit
	}
	config.Parameters = append([][2]string{}, config.Parameters...)
	config.Parameters = append(config.Parameters, [2]string{"stateless.debug.origin", "recorded-live-observation"}, [2]string{"stateless.debug.host_epoch", strconv.FormatUint(epoch, 10)})
	r, e := s.NewRecorderWithCheckedInitialAt(initial, after, config, record)
	if e != nil {
		return nil, e
	}
	b := &LiveBridge[S, I, O]{epoch: epoch, attached: after, host: after, complete: true, limits: limits, recorder: r, metadata: r.Snapshot().Metadata, maxChecks: record.Limits.MaxChecksPerStep}
	b.publish(LiveEvent{Kind: "checkpoint", FullChecking: true}, initial.Checks())
	return b, nil
}
func (b *LiveBridge[S, I, O]) publish(event LiveEvent, summaries ...[]s.Check) {
	if b.stream == math.MaxUint64 {
		satInc(&b.drops)
		return
	}
	b.stream++
	if len(b.events) >= b.limits.MaxEvents || len(b.events) >= b.limits.MaxEventBytes/128 {
		satInc(&b.drops)
		return
	}
	event.Epoch, event.StreamSequence, event.HostSequence = b.epoch, b.stream, b.host
	if len(summaries) > 0 {
		checks := summaries[0]
		n := min(uint64(len(checks)), b.maxChecks)
		event.ChecksOmitted = len(checks) - int(n)
		for _, c := range checks[:n] {
			if c.Status == "failed" {
				event.FailedChecks++
			}
		}
	}
	b.events = append(b.events, event)
}
func (b *LiveBridge[S, I, O]) Observe(epoch, sequence uint64, t *s.CheckedTurn[S, I, O]) error {
	if epoch != b.epoch {
		return ErrLiveAuthority
	}
	if t != nil {
		model, e := t.Model()
		if e != nil {
			return e
		}
		if model.Codec == nil || model.Codec.Metadata == nil {
			return ErrLivePhase
		}
		identity, e := model.Codec.Metadata()
		if e != nil {
			return e
		}
		if identity != b.metadata {
			b.StopRecording(errors.New("live model identity changed"))
			return ErrLiveAuthority
		}
	}
	if t == nil || t.Sequence() != sequence {
		return ErrLivePhase
	}
	if sequence <= b.host {
		satInc(&b.duplicates)
		return ErrLivePhase
	}
	transition, e := t.Transition()
	if e != nil {
		return e
	}
	gap := b.host == math.MaxUint64 || sequence != b.host+1
	if gap {
		b.complete = false
		b.recorder.StopWithError(errors.New("host sequence gap"))
		b.publish(LiveEvent{Kind: "gap"})
	}
	b.host = sequence
	var recording error
	if b.complete && !b.recorder.IsFrozen() {
		_, recording = s.ObserveCheckedRecorder(b.recorder, t)
		if recording != nil {
			b.complete = false
		}
	}
	if b.recorder.IsFrozen() || !b.complete {
		b.publish(LiveEvent{Kind: "recording-frozen"})
	}
	b.publish(LiveEvent{Kind: "turn", Outputs: len(transition.Outputs), FullChecking: t.FullChecking(), Disposition: transition.Disposition.Kind}, t.Checks())
	count := len(transition.Outputs)
	retained := min(count, b.limits.MaxEffectOrigins)
	skipped := count - retained
	evict := max(0, len(b.origins)-(b.limits.MaxEffectOrigins-retained))
	loss := uint64(skipped) + uint64(evict)
	if loss > math.MaxUint64-b.evictions {
		b.evictions = math.MaxUint64
	} else {
		b.evictions += loss
	}
	b.origins = b.origins[evict:]
	for i := skipped; i < count; i++ {
		b.origins = append(b.origins, bridgeOrigin{origin: EffectOrigin{b.epoch, sequence, i}})
	}

	if gap {
		return ErrLivePhase
	}
	return recording
}
func (b *LiveBridge[S, I, O]) EffectDispatched(origin EffectOrigin) error {
	if origin.Epoch != b.epoch {
		return ErrLiveAuthority
	}
	for i := range b.origins {
		p := &b.origins[i]
		if p.origin == origin {
			if p.dispatched {
				return ErrLivePhase
			}
			p.dispatched = true
			b.publish(LiveEvent{Kind: "effect-dispatched", Outputs: 1, FullChecking: true})
			return nil
		}
	}
	return ErrLivePhase
}
func (b *LiveBridge[S, I, O]) PopEvent() (LiveEvent, bool) {
	if len(b.events) == 0 {
		return LiveEvent{}, false
	}
	v := b.events[0]
	b.events = b.events[1:]
	return v, true
}
func (b *LiveBridge[S, I, O]) Health() LiveHealth {
	return LiveHealth{b.epoch, b.attached, b.host, b.complete, b.recorder.IsFrozen() || !b.complete, b.recorder.ObservedSteps(), b.recorder.RetainedSteps(), b.recorder.EvictedSteps(), b.drops, b.evictions, b.duplicates}
}
func (b *LiveBridge[S, I, O]) StopRecording(e error) { b.complete = false; b.recorder.StopWithError(e) }
func (b *LiveBridge[S, I, O]) Export() *s.AuditTrace { return b.recorder.Snapshot() }
