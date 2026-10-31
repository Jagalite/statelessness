import Foundation
import Statelessness

private func tableMetadata() -> FeedbackMetadata {
    FeedbackMetadata(name: "table-features", version: 1, build: "guided-v2-fixture-v1")
}

func customFeedback(_ spec: FeedbackSpec, events: Events) -> Feedback<TableState, Int64, Int64, FeatureKey> {
    func emit(_ values: [FeatureValue], error: String?, into out: FeatureBuffer<FeatureKey>) throws {
        for value in values {
            do {
                try out.push(FeatureKey(value: value, constantHash: spec.hash == .constant))
                events.trace.append(["push", value.json, true])
            } catch {
                events.trace.append(["push", value.json, false])
                if !spec.ignore_overrun { throw error }
            }
        }
        if let error { throw ModelError(error) }
    }

    // Omitting initialFeatures exercises the real public default no-op. A null
    // initial fixture must not produce an initial_feedback event or cancellation.
    var result = Feedback<TableState, Int64, Int64, FeatureKey>(metadata: {
        events.trace.append(["metadata"])
        defer { events.done("metadata") }
        return tableMetadata()
    }, cloneFeature: { $0 }, transitionFeatures: { before, input, after, out in
        events.trace.append(["transition_feedback", events.caseNumber, before.json, input, after.state.json, after.outputs, normalizedDisposition(after.disposition)])
        defer { events.done("transition_feedback") }
        if let row = spec.transitions.first(where: {
            ($0.case == nil || $0.case == events.caseNumber) && ($0.depth == nil || $0.depth == after.state.depth)
        }) {
            try emit(row.keys, error: row.error, into: out)
        }
    })
    if let initial = spec.initial {
        result.initialFeatures = { state, out in
            events.trace.append(["initial_feedback", events.caseNumber, state.json])
            defer { events.done("initial_feedback") }
            try emit(initial, error: spec.initial_error, into: out)
        }
    }
    return result
}

// Transparent callback wrappers around the public helpers. Internal pushes stay
// opaque; admission, feature tracking and mutation belong to the native engine.
func tracedFeedback<K: Hashable>(_ inner: Feedback<TableState, Int64, Int64, K>,
                                 events: Events) -> Feedback<TableState, Int64, Int64, K> {
    Feedback(metadata: {
        events.trace.append(["metadata"])
        defer { events.done("metadata") }
        return try inner.metadata()
    }, cloneFeature: inner.cloneFeature, initialFeatures: { state, out in
        events.trace.append(["initial_feedback", events.caseNumber, state.json])
        defer { events.done("initial_feedback") }
        try inner.initialFeatures(state, out)
    }, transitionFeatures: { before, input, after, out in
        events.trace.append(["transition_feedback", events.caseNumber, before.json, input, after.state.json, after.outputs, normalizedDisposition(after.disposition)])
        defer { events.done("transition_feedback") }
        try inner.transitionFeatures(before, input, after, out)
    })
}

func projectedFeedback(_ spec: FeedbackSpec, events: Events) -> Feedback<TableState, Int64, Int64, FeatureKey> {
    let projection = StateFeedback<TableState, FeatureKey>(metadata: tableMetadata, cloneFeature: { $0 }, project: { state in
        let value = spec.project == 0 ? state.depth : state.total
        return FeatureKey(value: .number(Decimal(value)), constantHash: spec.hash == .constant)
    })
    return tracedFeedback(projection.feedback(), events: events)
}
