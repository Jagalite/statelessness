package debug

// Optional local stdio transport. Hosts serialize access; this does not dispatch effects.
import (
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

type ProtocolError string

func (e ProtocolError) Error() string { return string(e) }

type ProtocolLimits struct {
	MaxFrame, MaxField, MaxFields, MaxInput, MaxCache, MaxCacheBytes, MaxHandles int
	MaxPage, MaxTelemetrySnapshot                                            int
}

func DefaultProtocolLimits() ProtocolLimits {
	return ProtocolLimits{MaxFrame: 65536, MaxField: 8192, MaxFields: 4096, MaxInput: 8192, MaxCache: 64, MaxCacheBytes: 4 << 20, MaxHandles: 64, MaxPage: 100, MaxTelemetrySnapshot: 8 << 20}
}
func (l ProtocolLimits) valid() bool {
	return l.MaxFrame >= 2048 && l.MaxField >= 64 && l.MaxFields >= 64 && l.MaxInput > 0 && l.MaxCache > 0 && l.MaxCacheBytes > 0 && l.MaxHandles > 0 && l.MaxPage > 0 && l.MaxTelemetrySnapshot > 0
}
func ReadFrame(r io.Reader, max int) ([]byte, error) {
	var h [4]byte
	n, e := io.ReadFull(r, h[:])
	if e != nil {
		if n == 0 && e == io.EOF {
			return nil, io.EOF
		}
		return nil, e
	}
	size := binary.BigEndian.Uint32(h[:])
	if size == 0 || uint64(size) > uint64(max) || max <= 0 {
		return nil, ProtocolError("limit")
	}
	b := make([]byte, int(size))
	_, e = io.ReadFull(r, b)
	if e == io.EOF {
		e = io.ErrUnexpectedEOF
	}
	return b, e
}
func WriteFrame(w io.Writer, b []byte, max int) error {
	if len(b) == 0 || len(b) > max || uint64(len(b)) > math.MaxUint32 {
		return ProtocolError("limit")
	}
	var h [4]byte
	binary.BigEndian.PutUint32(h[:], uint32(len(b)))
	for _, v := range [][]byte{h[:], b} {
		for len(v) > 0 {
			n, e := w.Write(v)
			if e != nil {
				return e
			}
			if n <= 0 || n > len(v) {
				return io.ErrShortWrite
			}
			v = v[n:]
		}
	}
	if flusher, ok := w.(interface{ Flush() error }); ok {
		return flusher.Flush()
	}
	return nil
}
func escapeField(s string) string {
	var b strings.Builder
	const digits = "0123456789ABCDEF"
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("-_.:", rune(c)) {
			b.WriteByte(c)
		} else {
			b.WriteByte('%')
			b.WriteByte(digits[c>>4])
			b.WriteByte(digits[c&15])
		}
	}
	return b.String()
}
func encodeFields(f []string, l ProtocolLimits) ([]byte, error) {
	if !l.valid() || len(f) > l.MaxFields {
		return nil, ProtocolError("limit")
	}
	out := make([]string, len(f))
	total := 0
	for i, s := range f {
		if len(s) > l.MaxField || !utf8.ValidString(s) {
			return nil, ProtocolError("limit")
		}
		out[i] = escapeField(s)
		total += len(out[i]) + 1
		if total-1 > l.MaxFrame {
			return nil, ProtocolError("limit")
		}
	}
	return []byte(strings.Join(out, "\t")), nil
}
func decodeFields(b []byte, l ProtocolLimits) ([]string, error) {
	if !l.valid() || len(b) > l.MaxFrame {
		return nil, ProtocolError("limit")
	}
	if !utf8.Valid(b) {
		return nil, ProtocolError("malformed")
	}
	f := strings.Split(string(b), "\t")
	for i, s := range f {
		if i == l.MaxFields {
			return nil, ProtocolError("limit")
		}
		if len(s) > l.MaxField*3 {
			return nil, ProtocolError("limit")
		}
		var v []byte
		for j := 0; j < len(s); j++ {
			c := s[j]
			if c == '%' {
				if j+2 >= len(s) {
					return nil, ProtocolError("malformed")
				}
				x, e := hex.DecodeString(s[j+1 : j+3])
				if e != nil {
					return nil, ProtocolError("malformed")
				}
				v = append(v, x[0])
				j += 2
			} else {
				if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("-_.:", rune(c))) {
					return nil, ProtocolError("malformed")
				}
				v = append(v, c)
			}
			if len(v) > l.MaxField {
				return nil, ProtocolError("limit")
			}
		}
		if !utf8.Valid(v) {
			return nil, ProtocolError("malformed")
		}
		f[i] = string(v)
	}
	return f, nil
}
func number(v uint64) string { return "u64:" + strconv.FormatUint(v, 10) }
func parseNumber(s string) (uint64, error) {
	if !strings.HasPrefix(s, "u64:") || len(s) == 4 {
		return 0, ProtocolError("malformed")
	}
	for _, c := range s[4:] {
		if c < '0' || c > '9' {
			return 0, ProtocolError("malformed")
		}
	}
	v, e := strconv.ParseUint(s[4:], 10, 64)
	if e != nil {
		return 0, ProtocolError("malformed")
	}
	return v, nil
}

type ProtocolRequest struct {
	Session                            string
	Epoch, ID, Revision, Configuration uint64
	Command                            string
	Args                               []string
}
type ProtocolField struct{ Key, Value string }
type ProtocolResponse struct {
	Session                            string
	Epoch, ID, Revision, Configuration uint64
	Error                              string
	Fields                             []ProtocolField
}

func EncodeRequest(r ProtocolRequest, l ProtocolLimits) ([]byte, error) {
	f := []string{"DDBG", "u32:1", "request", "string:" + r.Session, number(r.Epoch), number(r.ID), number(r.Revision), number(r.Configuration), r.Command}
	l.MaxFields = min(l.MaxFields, 64)
	return encodeFields(append(f, r.Args...), l)
}
func DecodeRequest(b []byte, l ProtocolLimits) (ProtocolRequest, error) {
	var r ProtocolRequest
	l.MaxFields = min(l.MaxFields, 64)
	f, e := decodeFields(b, l)
	if e != nil {
		return r, e
	}
	if len(f) < 9 || f[0] != "DDBG" || f[2] != "request" {
		return r, ProtocolError("malformed")
	}
	if !strings.HasPrefix(f[1], "u32:") {
		return r, ProtocolError("malformed")
	}
	version, e := strconv.ParseUint(strings.TrimPrefix(f[1][4:], "+"), 10, 32)
	if e != nil {
		return r, ProtocolError("malformed")
	}
	if version != 1 {
		return r, ProtocolError("unsupported_version")
	}
	r.Command = f[8]
	r.Args, e = commandArguments(r.Command, f[9:], l)
	if e != nil {
		return r, e
	}
	if !strings.HasPrefix(f[3], "string:") {
		return r, ProtocolError("malformed")
	}
	r.Session = f[3][7:]
	p := []*uint64{&r.Epoch, &r.ID, &r.Revision, &r.Configuration}
	for i, v := range p {
		*v, e = parseNumber(f[4+i])
		if e != nil {
			return r, e
		}
	}
	return r, nil
}
func EncodeResponse(r ProtocolResponse, l ProtocolLimits) ([]byte, error) {
	if len(r.Fields) > l.MaxFields {
		return nil, ProtocolError("limit")
	}
	l.MaxFields = l.MaxFields*2 + 11
	status, category := "ok", "none"
	if r.Error != "" {
		status, category = "error", r.Error
	}
	f := []string{"DDBG", "u32:1", "response", "string:" + r.Session, number(r.Epoch), number(r.ID), number(r.Revision), number(r.Configuration), status, category, number(uint64(len(r.Fields)))}
	for _, v := range r.Fields {
		f = append(f, v.Key, v.Value)
	}
	return encodeFields(f, l)
}
func DecodeResponse(b []byte, l ProtocolLimits) (ProtocolResponse, error) {
	var r ProtocolResponse
	l.MaxFields = l.MaxFields*2 + 11
	f, e := decodeFields(b, l)
	if e != nil {
		return r, e
	}
	if len(f) < 11 || f[0] != "DDBG" || f[1] != "u32:1" || f[2] != "response" || !strings.HasPrefix(f[3], "string:") {
		return r, ProtocolError("malformed")
	}
	r.Session = f[3][7:]
	for i, p := range []*uint64{&r.Epoch, &r.ID, &r.Revision, &r.Configuration} {
		*p, e = parseNumber(f[4+i])
		if e != nil {
			return r, e
		}
	}
	if f[8] == "error" && f[9] != "none" {
		r.Error = f[9]
	} else if f[8] != "ok" || f[9] != "none" {
		return r, ProtocolError("malformed")
	}
	n, e := parseNumber(f[10])
	if e != nil || n > uint64((len(f)-11)/2) || int(n)*2 != len(f)-11 {
		return r, ProtocolError("malformed")
	}
	for i := 11; i < len(f); i += 2 {
		r.Fields = append(r.Fields, ProtocolField{f[i], f[i+1]})
	}
	return r, nil
}

type ProtocolAuthority struct{ DeliverInputs, ConfigureWatches, ConfigureDiagnostics, ReadExactValues, ExportExactTrace bool }
type protocolCache struct {
	request  string
	response ProtocolResponse
	size     int
}
type ProtocolSession[S, I, O any] struct {
	busy                    bool
	connected               bool
	configuration           uint64
	adapters                map[string]ProtocolAdapter
	session                 *Session[S, I, O]
	decode                  func([]byte) (I, error)
	authority               ProtocolAuthority
	limits                  ProtocolLimits
	epoch, high, nextHandle uint64
	cache                   map[uint64]protocolCache
	order                   []uint64
	cacheBytes              int
	handles                 map[uint64]uint64
	handleOrder             []uint64
}

func NewProtocolSession[S, I, O any](s *Session[S, I, O], decode func([]byte) (I, error), a ProtocolAuthority, l ProtocolLimits) (*ProtocolSession[S, I, O], error) {
	if s == nil || !l.valid() {
		return nil, fmt.Errorf("invalid protocol host or limits")
	}
	p := &ProtocolSession[S, I, O]{session: s, decode: decode, authority: a, limits: l}
	if e := p.Reconnect(); e != nil {
		return nil, e
	}
	return p, nil
}
func (p *ProtocolSession[S, I, O]) Epoch() uint64 { return p.epoch }

// Disconnect closes protocol admission without cancelling the modeled session
// or changing its state, revision or sequence. Repeated calls are harmless.
func (p *ProtocolSession[S, I, O]) Disconnect() { p.connected = false }

// Reconnect preserves the session, authority and configuration. It requires a
// disconnected connection and starts fresh request admission in a new epoch.
// Snapshot identifiers remain monotonic so an old handle cannot be revived.
func (p *ProtocolSession[S, I, O]) Reconnect() error {
	if p.busy {
		return ProtocolError("busy")
	}
	if p.connected {
		return ProtocolError("invalid_input")
	}
	for {
		old := epochs.Load()
		if old == math.MaxUint64 {
			return ProtocolError("exhausted")
		}
		if epochs.CompareAndSwap(old, old+1) {
			p.epoch = old + 1
			break
		}
	}
	p.high = 0
	p.cache = map[uint64]protocolCache{}
	p.order = nil
	p.cacheBytes = 0
	p.handles = map[uint64]uint64{}
	p.handleOrder = nil
	p.connected = true
	return nil
}
func cloneResponse(r ProtocolResponse) ProtocolResponse {
	r.Fields = append([]ProtocolField{}, r.Fields...)
	return r
}
func (p *ProtocolSession[S, I, O]) Handle(r ProtocolRequest) ProtocolResponse {
	out := ProtocolResponse{Session: p.session.ID(), Epoch: p.epoch, ID: r.ID, Revision: p.session.Revision(), Configuration: p.configuration}
	fail := func(s string) ProtocolResponse { out.Error = s; out.Fields = nil; return out }
	if p.busy {
		return fail("busy")
	}
	p.busy = true
	defer func() { p.busy = false }()
	if r.Session != p.session.ID() {
		return fail("wrong_session")
	}
	if r.Epoch == 0 && r.Command == "status" {
		r.Epoch = p.epoch
	}
	if r.Epoch != p.epoch {
		return fail("wrong_epoch")
	}
	if !p.connected {
		return fail("disconnected")
	}
	if r.ID == 0 {
		return fail("malformed")
	}
	args, e := commandArguments(r.Command, r.Args, p.limits)
	if e != nil {
		return fail(e.Error())
	}
	r.Args = args
	raw, e := EncodeRequest(r, p.limits)
	if e != nil {
		return fail("limit")
	}
	key := string(raw)
	if c, ok := p.cache[r.ID]; ok {
		if c.request != key {
			return fail("duplicate_conflict")
		}
		return cloneResponse(c.response)
	}
	if r.ID <= p.high {
		return fail("retired_request")
	}
	p.high = r.ID
	out = p.execute(r, out)
	out.Revision = p.session.Revision()
	out.Configuration = p.configuration
	wire, err := EncodeResponse(out, p.limits)
	if err != nil {
		out = fail("limit")
		wire, _ = EncodeResponse(out, p.limits)
	}
	size := len(raw) + len(wire) + 128
	for len(p.order) > 0 && (len(p.order) >= p.limits.MaxCache || p.cacheBytes+size > p.limits.MaxCacheBytes) {
		id := p.order[0]
		p.order = p.order[1:]
		p.cacheBytes -= p.cache[id].size
		delete(p.cache, id)
	}
	if size <= p.limits.MaxCacheBytes {
		p.cache[r.ID] = protocolCache{key, cloneResponse(out), size}
		p.order = append(p.order, r.ID)
		p.cacheBytes += size
	}
	return cloneResponse(out)
}
func (p *ProtocolSession[S, I, O]) execute(r ProtocolRequest, out ProtocolResponse) ProtocolResponse {
	fail := func(s string) ProtocolResponse { out.Error = s; out.Fields = nil; return out }
	field := func(k, v string) { out.Fields = append(out.Fields, ProtocolField{k, v}) }
	reserve := func(fields []ProtocolField) bool {
		prospective := out
		prospective.Revision = math.MaxUint64
		prospective.Fields = fields
		_, err := EncodeResponse(prospective, p.limits)
		return err == nil
	}
	switch r.Command {
	case "status":
		if len(r.Args) != 0 {
			return fail("malformed")
		}
		return p.status(out)
	case "snapshot":
		if r.Revision != p.session.Revision() {
			return fail("stale_revision")
		}
		if len(r.Args) != 0 {
			return fail("malformed")
		}
		if p.nextHandle == math.MaxUint64 {
			return fail("exhausted")
		}
		if !reserve([]ProtocolField{{"handle", number(math.MaxUint64)}, {"snapshot.revision", number(math.MaxUint64)}, {"snapshot.sequence", number(math.MaxUint64)}}) {
			return fail("limit")
		}
		p.nextHandle++
		id := p.nextHandle
		if len(p.handleOrder) == p.limits.MaxHandles {
			delete(p.handles, p.handleOrder[0])
			p.handleOrder = p.handleOrder[1:]
		}
		p.handleOrder = append(p.handleOrder, id)
		p.handles[id] = p.session.Revision()
		field("handle", number(id))
		field("snapshot.revision", number(p.session.Revision()))
		field("snapshot.sequence", number(p.session.Sequence()))
	case "checks", "outputs":
		if len(r.Args) != 1 {
			return fail("malformed")
		}
		h, e := parseNumber(r.Args[0])
		if e != nil {
			return fail("malformed")
		}
		v, ok := p.handles[h]
		if !ok || v != p.session.Revision() {
			return fail("stale_handle")
		}
		if p.adapters[r.Command] != nil {
			return p.adapter(r, out)
		}
		return p.observationFields(r.Command, out)
	case "step":
		if !p.authority.DeliverInputs {
			return fail("unauthorized")
		}
		if r.Revision != p.session.Revision() {
			return fail("stale_revision")
		}
		if len(r.Args) != 1 || !strings.HasPrefix(r.Args[0], "bytes:") {
			return fail("malformed")
		}
		if p.decode == nil {
			return fail("unsupported")
		}
		if len(r.Args[0])-6 > 2*p.limits.MaxInput {
			return fail("limit")
		}
		if !reserve([]ProtocolField{{"delivered", "bool:false"}, {"sequence", number(math.MaxUint64)}}) {
			return fail("limit")
		}
		b, e := hex.DecodeString(r.Args[0][6:])
		if e != nil {
			return fail("invalid_input")
		}
		input, e := func() (I, error) {
			old := p.session.busy
			p.session.busy = true
			defer func() { p.session.busy = old }()
			return p.decode(b)
		}()
		if e != nil {
			return fail("invalid_input")
		}
		if p.session.Phase() == Ended {
			return fail("ended")
		}
		result, e := p.session.Step(r.Revision, input)
		if e != nil {
			return fail("invalid_input")
		}
		field("delivered", "bool:"+strconv.FormatBool(result.Delivered))
		field("sequence", number(result.Sequence))
	case "cancel":
		if !p.authority.DeliverInputs {
			return fail("unauthorized")
		}
		if r.Revision != p.session.Revision() {
			return fail("stale_revision")
		}
		if len(r.Args) != 0 {
			return fail("malformed")
		}
		if p.session.Phase() == Ended {
			return fail("ended")
		}
		if !reserve(nil) {
			return fail("limit")
		}
		if e := p.session.Cancel(r.Revision); e != nil {
			return fail("callback")
		}
	default:
		return p.adapter(r, out)
	}
	return out
}

// Serve disconnects on clean EOF and every transport error without cancelling
// the model. A failed write may follow a committed transition; reconnect needs
// state reconciliation rather than blindly retrying an input in the new epoch.
func (p *ProtocolSession[S, I, O]) Serve(r io.Reader, w io.Writer) error {
	defer p.Disconnect()
	for {
		b, e := ReadFrame(r, p.limits.MaxFrame)
		if e == io.EOF {
			return nil
		}
		if e != nil {
			return e
		}
		q, e := DecodeRequest(b, p.limits)
		var out ProtocolResponse
		if e != nil {
			out = ProtocolResponse{Session: p.session.ID(), Epoch: p.epoch, Revision: p.session.Revision(), Configuration: p.configuration, Error: e.Error()}
		} else {
			out = p.Handle(q)
		}
		wire, e := EncodeResponse(out, p.limits)
		if e != nil {
			return e
		}
		if e = WriteFrame(w, wire, p.limits.MaxFrame); e != nil {
			return e
		}
	}
}

// ProtocolPrepared stages a host response before mutation. Prepare must be read-only;
// Commit is invoked once only after the entire response passes wire budgets.
// Host callbacks remain cooperative and must enforce their own capture/work bounds.
type ProtocolPrepared struct {
	Fields []ProtocolField
	Commit func() error
}
type ProtocolAdapter func(ProtocolRequest) (ProtocolPrepared, error)
type commandShape struct {
	tags          []string
	path          bool
	authority     string
	configuration bool
}

func adapterShape(command string) (commandShape, bool) {
	if command == "probe_configure" {
		return commandShape{tags: []string{"u64:"}, authority: "diagnostics"}, true
	}
	if command == "metric_snapshot" {
		return commandShape{tags: []string{"handle", "u64:", "u64:"}}, true
	}
	m := map[string]commandShape{
		"checks": {tags: []string{"u64:"}}, "outputs": {tags: []string{"u64:"}},
		"inspect":      {tags: []string{"u64:", "u64:", "u64:", "u64:"}, path: true},
		"export_trace": {tags: []string{"u64:", "u64:", "u64:"}, authority: "export"},
		"inputs":       {tags: []string{"u64:", "u64:"}}, "select": {tags: []string{"u64:"}, authority: "deliver"},
		"events": {tags: []string{"u64:"}}, "watch": {tags: []string{"u64:", "u64:"}, path: true, authority: "watch", configuration: true},
		"unwatch": {tags: []string{"u64:"}, authority: "watch", configuration: true}, "watch_status": {}, "watch_current": {tags: []string{"u64:"}},
		"metric_catalog": {tags: []string{"u64:", "u64:"}}, "metric_window": {tags: []string{"u64:", "u64:", "u64:"}},
		"effect_details": {tags: []string{"u64:", "u64:", "u64:"}}, "effect_timeline": {tags: []string{"u64:", "u64:", "u64:", "u64:"}}, "telemetry_health": {},
		"probe_status": {tags: []string{"u64:", "u64:"}}, "probe_events": {tags: []string{"u64:", "u64:", "bool:"}},
	}
	v, ok := m[command]
	return v, ok
}

// RegisterAdapter exposes only known protocol command families, never arbitrary commands.
// An installed callback cannot be replaced during a callback or by a client command.
func (p *ProtocolSession[S, I, O]) RegisterAdapter(command string, adapter ProtocolAdapter) error {
	if p.busy {
		return ProtocolError("busy")
	}
	if _, ok := adapterShape(command); !ok {
		return ProtocolError("unsupported")
	}
	if adapter == nil {
		return ProtocolError("malformed")
	}
	if p.adapters == nil {
		p.adapters = map[string]ProtocolAdapter{}
	}
	if _, ok := p.adapters[command]; ok {
		return ProtocolError("busy")
	}
	p.adapters[command] = adapter
	return nil
}
func (p *ProtocolSession[S, I, O]) adapter(r ProtocolRequest, out ProtocolResponse) ProtocolResponse {
	fail := func(category string) ProtocolResponse { out.Error = category; out.Fields = nil; return out }
	shape, ok := adapterShape(r.Command)
	if !ok {
		return fail("unsupported")
	}
	args, err := commandArguments(r.Command, r.Args, p.limits)
	if err != nil {
		return fail(err.Error())
	}
	r.Args = args
	allowed := true
	switch shape.authority {
	case "deliver":
		allowed = p.authority.DeliverInputs
	case "watch":
		allowed = p.authority.ConfigureWatches
	case "diagnostics":
		allowed = p.authority.ConfigureDiagnostics
	case "export":
		allowed = p.authority.ExportExactTrace
	}
	if !allowed {
		return fail("unauthorized")
	}
	if r.Command == "inspect" || r.Command == "export_trace" {
		h, _ := parseNumber(r.Args[0])
		rev, ok := p.handles[h]
		if !ok || rev != p.session.Revision() {
			return fail("stale_handle")
		}
	}
	if shape.configuration && r.Configuration != p.configuration {
		return fail("stale_configuration")
	}
	switch r.Command {
	case "inspect", "export_trace", "inputs", "select", "watch":
		if r.Revision != p.session.Revision() {
			return fail("stale_revision")
		}
	}
	if r.Command == "watch" {
		baseline, _ := parseNumber(r.Args[1])
		if baseline != p.session.Revision() {
			return fail("stale_revision")
		}
	}
	host := p.adapters[r.Command]
	if host == nil {
		return fail("unsupported")
	}
	if shape.configuration && p.configuration == math.MaxUint64 {
		return fail("exhausted")
	}
	r.Args = append([]string{}, r.Args...)
	prepared, e := func() (ProtocolPrepared, error) {
		old := p.session.busy
		p.session.busy = true
		defer func() { p.session.busy = old }()
		return host(r)
	}()
	if e != nil {
		if category, ok := e.(ProtocolError); ok {
			return fail(string(category))
		}
		return fail("callback")
	}
	out.Fields = append([]ProtocolField{}, prepared.Fields...)
	if shape.configuration {
		out.Configuration = p.configuration + 1
	}
	// A committed select may advance the model revision. Reserve its widest
	// wire representation before granting the mutation capability.
	reserved := out
	reserved.Revision = math.MaxUint64
	reserved.Configuration = math.MaxUint64
	if _, e = EncodeResponse(reserved, p.limits); e != nil {
		return fail("limit")
	}
	if prepared.Commit != nil {
		if e = prepared.Commit(); e != nil {
			return fail("callback")
		}
	}
	if shape.configuration {
		p.configuration++
	}
	return out
}

func validIntegerKey(kind, value string) bool {
	bits, e := strconv.Atoi(kind[5:])
	if e != nil {
		return false
	}
	n, ok := new(big.Int).SetString(value, 10)
	if !ok {
		return false
	}
	if kind[4] == 'u' {
		return n.Sign() >= 0 && n.BitLen() <= bits
	}
	bound := new(big.Int).Lsh(big.NewInt(1), uint(bits-1))
	return n.Cmp(new(big.Int).Neg(new(big.Int).Set(bound))) >= 0 && n.Cmp(bound) < 0
}
