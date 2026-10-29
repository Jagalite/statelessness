import Statelessness

func asynchronousFeedbackIsNotAccepted() {
    _ = Feedback<Int, Int, Int, Int>(
        metadata: { FeedbackMetadata(name: "async", build: "must-not-compile") },
        cloneFeature: { $0 },
        transitionFeatures: { _, _, _, _ in await Task.yield() })
}
