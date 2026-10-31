package debug

import "math"

// EnableInspection exposes an explicit display-only state projection over the
// protocol. Inspection never uses the model's state codec as a projection.
func (p *ProtocolSession[S, I, O]) EnableInspection(project func(S) InspectAdapter) error {
	if project == nil {
		return ProtocolError("malformed")
	}
	return p.RegisterAdapter("inspect", func(r ProtocolRequest) (ProtocolPrepared, error) {
		schema, _ := parseNumber(r.Args[1])
		offset, _ := parseNumber(r.Args[2])
		limit, _ := parseNumber(r.Args[3])
		if limit == 0 || limit > uint64(p.limits.MaxPage) || offset > math.MaxUint64-limit {
			return ProtocolPrepared{}, ProtocolError("limit")
		}
		adapter, err := watchAdapterSafely(func() InspectAdapter { return project(p.session.State()) })
		if err != nil {
			return ProtocolPrepared{}, ProtocolError("callback")
		}
		snapshot := SnapshotID{p.session.Epoch(), p.session.Revision(), p.session.Sequence()}
		result, err := inspect(adapter, InspectQuery{
			Snapshot: snapshot, SchemaVersion: uint32(schema), Path: protocolPath(r.Args[4:]), Offset: offset, Limit: limit,
			Limits: InspectLimits{MaxWork: 10000, MaxDepth: 16, MaxNodes: 128, MaxBytes: uint64(p.limits.MaxFrame / 8), MaxValueBytes: uint64(p.limits.MaxField / 4), MaxPageSize: uint64(p.limits.MaxPage)},
		}, true)
		if err != nil {
			return ProtocolPrepared{}, ProtocolError("invalid_input")
		}
		if len(result.Schema.Name) > p.limits.MaxField-7 {
			return ProtocolPrepared{}, ProtocolError("limit")
		}
		fields := []ProtocolField{{"schema.name", "string:" + result.Schema.Name}, {"schema.version", number(uint64(result.Schema.Version))}, {"snapshot.revision", number(snapshot.Revision)}, {"snapshot.sequence", number(snapshot.Sequence)}}
		nodes, err := protocolNode("node", result.Node)
		if err != nil {
			return ProtocolPrepared{}, err
		}
		return ProtocolPrepared{Fields: append(fields, nodes...)}, nil
	})
}
