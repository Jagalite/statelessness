import Statelessness

func requiresSendable<T: Sendable>(_ value: T) {}
func featureSinkIsNotSendable(_ sink: FeatureBuffer<Int>) {
    requiresSendable(sink)
}
