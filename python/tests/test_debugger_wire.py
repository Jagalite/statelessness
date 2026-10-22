"""Source-derived public Rust wire regressions; never implementation goldens.

Expected fields/order below come from protocol.rs and session.rs contracts.
Execution and installed-wheel qualification belong to the root Kyoto job.
"""
import io
import unittest
from dataclasses import replace

from statelessness import Check, Disposition, ModelError, Transition
from statelessness.debugger import InputPolicy
from statelessness.debugger_protocol import (
    ProtocolAuthority, ProtocolError, ProtocolLimits, ProtocolSession, Request,
    decode_request, decode_response, encode_request, read_frame, serve, write_frame,
)
from statelessness.debugger_session import DebugSession


class WireModel:
    def __init__(self):
        self.calls = []

    def initial_state(self):
        self.calls.append(('initial',))
        return 0

    def check_state(self, state):
        self.calls.append(('state', state))
        return (Check('bounded') if state <= 2 else
                Check('bounded', 'failed', 'private state detail'),)

    def check_transition(self, before, input, transition):
        self.calls.append(('transition', before, input, transition.state))
        return (Check('transition'),)

    def step(self, state, input):
        self.calls.append(('step', state, input))
        if input == 0:
            return Transition(state, disposition=Disposition('ignored'))
        if input == 1:
            return Transition(state + 1, outputs=(state + 1,))
        if input == 2:
            return Transition(state, disposition=Disposition('rejected', 'fixture rejection'))
        raise ModelError('private reducer detail')

    def decode_input(self, value):
        self.calls.append(('decode', value))
        if len(value) != 1 or value[0] > 3:
            raise ModelError('invalid input')
        return value[0]

    def encode_output_into(self, output, sink):
        sink.append(output.to_bytes(8, 'little'))


class DirectWireTests(unittest.TestCase):
    def make(self, *, deliver=True, exact=False, cache=64):
        model = WireModel()
        session = DebugSession('wire\té😀', model, InputPolicy('out-of-domain-injection', unrestricted=True))
        return model, ProtocolSession(
            session, decode_input=model.decode_input, encode_output_into=model.encode_output_into,
            authority=ProtocolAuthority(deliver_inputs=deliver, read_exact_values=exact),
            limits=ProtocolLimits(max_cache_entries=cache),
        )

    def request(self, protocol, id, command='status', args=(), revision=None):
        return Request(protocol.session.id, protocol.epoch, id,
                       protocol.session.revision if revision is None else revision, 0, command, args)

    def fields(self, response):
        self.assertIsNone(response.error)
        fields = dict(response.fields)
        self.assertEqual(len(fields), len(response.fields), 'duplicate response keys')
        return fields

    def exchange(self, protocol, payloads):
        incoming, outgoing = io.BytesIO(), io.BytesIO()
        for payload in payloads:
            write_frame(incoming, payload)
        incoming.seek(0)
        serve(protocol, incoming, outgoing)
        outgoing.seek(0)
        responses = []
        while (payload := read_frame(outgoing)) is not None:
            responses.append(decode_response(payload))
        return responses

    def test_complete_initial_status_contract(self):
        model, p = self.make(exact=True, cache=1)
        response = p.handle(self.request(p, 1, revision=(1 << 64) - 1))
        self.assertEqual(self.fields(response), {
            'mode': 'enum:simulation', 'phase': 'enum:Ready', 'sequence': 'u64:0',
            'environment': 'string:out-of-domain-injection',
            'capability.input_delivery': 'bool:true', 'capability.inspection': 'bool:false',
            'capability.watch_configuration': 'bool:false', 'capability.exact_values': 'bool:true',
            'capability.live_control': 'bool:false', 'capability.live_restore': 'bool:false',
            'capability.effects': 'bool:false', 'capability.probe_queries': 'bool:false',
            'capability.probe_configuration': 'bool:false', 'capability.trace_export': 'bool:false',
            'capability.effect_telemetry': 'bool:false', 'capability.metric_queries': 'bool:false',
            'limits.telemetry_snapshot_bytes': 'u64:8388608', 'limits.frame_bytes': 'u64:65536',
            'limits.dedup_entries': 'u64:1', 'limits.dedup_bytes': 'u64:4194304',
            'limits.page_size': 'u64:100', 'events.dropped': 'u64:0', 'stop': 'enum:none',
        })
        self.assertEqual(model.calls, [('initial',), ('state', 0)])

    def test_reconnect_requires_disconnect_and_cannot_revive_handles(self):
        model, p = self.make()
        request = self.request(p, 1, 'step', ('bytes:01',))
        committed = p.handle(request)
        self.fields(committed)
        old_handle = self.fields(p.handle(self.request(p, 2, 'snapshot')))['handle']
        epoch, calls = p.epoch, list(model.calls)
        with self.assertRaises(ProtocolError) as error:
            p.reconnect()
        self.assertEqual(error.exception.code, 'invalid_input')
        self.assertEqual(p.epoch, epoch)
        self.assertEqual(p.handle(request), committed)
        self.assertEqual(model.calls, calls)
        self.assertEqual((p.session.state, p.session.revision, p.session.sequence), (1, 1, 1))
        p.disconnect()
        self.assertEqual(p.handle(self.request(p, 3)).error, 'disconnected')
        self.assertNotEqual(p.reconnect(), epoch)
        new_handle = self.fields(p.handle(self.request(p, 1, 'snapshot')))['handle']
        self.assertEqual((old_handle, new_handle), ('u64:1', 'u64:2'))
        self.assertEqual(p.handle(self.request(p, 2, 'checks', (old_handle,))).error, 'stale_handle')
        self.fields(p.handle(self.request(p, 3, 'checks', (new_handle,))))
        self.assertEqual(model.calls, calls)

    def test_snapshot_checks_outputs_and_command_revision_order(self):
        model, p = self.make()
        self.assertEqual(self.fields(p.handle(self.request(p, 1, 'snapshot'))), {
            'handle': 'u64:1', 'snapshot.revision': 'u64:0', 'snapshot.sequence': 'u64:0',
        })
        self.assertEqual(self.fields(p.handle(self.request(p, 2, 'checks', ('u64:1',), revision=999))), {
            'checks.complete': 'bool:true', 'checks.total': 'u64:1',
            'checks.first_failure': 'none', 'checks.returned': 'u64:1',
            'checks.truncated': 'bool:false', 'checks.0.id': 'string:bounded',
            'checks.0.status': 'enum:passed',
        })
        self.assertEqual(self.fields(p.handle(self.request(p, 3, 'outputs', ('u64:1',), revision=999))),
                         {'outputs.total': 'u64:0'})
        self.assertEqual(p.handle(self.request(p, 4, 'snapshot', revision=999)).error, 'stale_revision')
        self.assertEqual(self.fields(p.handle(self.request(p, 5, 'step', ('bytes:01',)))),
                         {'delivered': 'bool:true', 'sequence': 'u64:1'})
        for id, command in ((6, 'checks'), (7, 'outputs'), (8, 'inspect')):
            args = ('u64:1',) if command != 'inspect' else ('u64:1', 'u64:1', 'u64:0', 'u64:1')
            self.assertEqual(p.handle(self.request(p, id, command, args, revision=999)).error, 'stale_handle')
        self.assertEqual(model.calls, [('initial',), ('state', 0), ('decode', b'\x01'),
                                      ('step', 0, 1), ('state', 1), ('transition', 0, 1, 1)])

    def test_authority_precedes_stale_revision_and_decode(self):
        model, p = self.make(deliver=False)
        for id, command, args in ((1, 'step', ('bytes:01',)), (2, 'cancel', ()),
                                  (3, 'select', ('u64:1',)),
                                  (4, 'export_trace', ('u64:1', 'u64:0', 'u64:1')),
                                  (5, 'watch', ('u64:1', 'u64:0')),
                                  (6, 'probe_configure', ('u64:0',)),
                                  (7, 'unwatch', ('u64:1',))):
            self.assertEqual(p.handle(self.request(p, id, command, args, revision=999)).error, 'unauthorized')
            self.assertEqual(p.handle(self.request(p, id)).error, 'duplicate_conflict')
        self.assertEqual(model.calls, [('initial',), ('state', 0)])

    def test_cancelled_session_decodes_before_ended_and_keeps_revision(self):
        model, p = self.make()
        self.assertEqual(self.fields(p.handle(self.request(p, 1, 'cancel'))), {})
        self.assertEqual(p.handle(self.request(p, 2, 'step', ('bytes:01',))).error, 'ended')
        self.assertEqual(p.handle(self.request(p, 3, 'step', ('bytes:ff',))).error, 'invalid_input')
        self.assertEqual(p.handle(self.request(p, 4, 'step', ('bytes:01',), revision=999)).error, 'stale_revision')
        self.assertEqual(model.calls, [('initial',), ('state', 0), ('decode', b'\x01'), ('decode', b'\xff')])
        fields = self.fields(p.handle(self.request(p, 5)))
        self.assertEqual((fields['phase'], fields['stop'], fields['sequence']),
                         ('enum:Ended', 'enum:cancelled', 'u64:0'))

    def test_dispositions_exact_outputs_and_failed_state_check(self):
        model, p = self.make(exact=True)
        id = 0
        for sequence, input, after, disposition in ((1, 0, 0, 'ignored'), (2, 2, 0, 'rejected'),
                                                   (3, 1, 1, 'accepted'), (4, 1, 2, 'accepted'),
                                                   (5, 1, 3, 'accepted')):
            id += 1
            response = p.handle(self.request(p, id, 'step', (f'bytes:{input:02x}',)))
            self.assertEqual(self.fields(response), {'delivered': 'bool:true', 'sequence': f'u64:{sequence}'})
            self.assertEqual(response.revision, sequence)
            id += 1
            handle = self.fields(p.handle(self.request(p, id, 'snapshot')))['handle']
            id += 1
            expected = {'outputs.total': 'u64:1' if input == 1 else 'u64:0',
                        'disposition': 'enum:' + disposition, 'outputs.provenance': 'enum:simulated',
                        'outputs.payloads': 'enum:exact_sensitive', 'outputs.truncated': 'bool:false'}
            if input == 1:
                expected['outputs.0.encoded'] = 'bytes:' + after.to_bytes(8, 'little').hex()
            self.assertEqual(self.fields(p.handle(self.request(p, id, 'outputs', (handle,)))), expected)
        id += 1
        self.assertEqual(self.fields(p.handle(self.request(p, id, 'checks', (handle,)))), {
            'checks.complete': 'bool:true', 'checks.total': 'u64:2',
            'checks.first_failure.id': 'string:bounded', 'checks.first_failure.index': 'u64:0',
            'checks.first_failure.phase': 'enum:post_state', 'checks.first_failure.sequence': 'u64:5',
            'checks.returned': 'u64:2', 'checks.truncated': 'bool:false',
            'checks.0.id': 'string:bounded', 'checks.0.status': 'enum:failed',
            'checks.1.id': 'string:transition', 'checks.1.status': 'enum:passed',
        })
        fields = self.fields(p.handle(self.request(p, id + 1)))
        self.assertEqual((fields['phase'], fields['stop']), ('enum:Ended', 'enum:property_failure'))
        self.assertEqual(model.calls[-4:], [('decode', b'\x01'), ('step', 2, 1),
                                          ('state', 3), ('transition', 2, 1, 3)])

    def test_reducer_error_keeps_previous_observation(self):
        model, p = self.make()
        self.fields(p.handle(self.request(p, 1, 'step', ('bytes:01',))))
        handle = self.fields(p.handle(self.request(p, 2, 'snapshot')))['handle']
        checks = self.fields(p.handle(self.request(p, 3, 'checks', (handle,))))
        outputs = self.fields(p.handle(self.request(p, 4, 'outputs', (handle,))))
        self.assertEqual(self.fields(p.handle(self.request(p, 5, 'step', ('bytes:03',)))),
                         {'delivered': 'bool:false', 'sequence': 'u64:1'})
        self.assertEqual(model.calls[-2:], [('decode', b'\x03'), ('step', 1, 3)])
        self.assertEqual(self.fields(p.handle(self.request(p, 6, 'checks', (handle,)))), checks)
        self.assertEqual(self.fields(p.handle(self.request(p, 7, 'outputs', (handle,)))), outputs)
        fields = self.fields(p.handle(self.request(p, 8)))
        self.assertEqual((fields['phase'], fields['stop'], fields['sequence']),
                         ('enum:Ended', 'enum:model_error', 'u64:1'))
        self.assertEqual(p.session.revision, 1)

    def test_event_drop_status_tracks_real_bounded_publication(self):
        model, p = self.make()
        p.limits = replace(p.limits, max_event_entries=1)
        first = self.request(p, 1, 'step', ('bytes:01',))
        response = p.handle(first)
        self.assertEqual(p.handle(first), response)
        self.fields(p.handle(self.request(p, 2, 'step', ('bytes:00',))))
        self.assertEqual(self.fields(p.handle(self.request(p, 3)))['events.dropped'], 'u64:1')
        poll = self.request(p, 4, 'events', ('u64:1',), revision=(1 << 64) - 1)
        response = p.handle(poll)
        self.assertEqual(self.fields(response), {
            'events.dropped': 'u64:1', 'events.returned': 'u64:1',
            'events.0.version': 'u64:1', 'events.0.session': 'string:wire\té😀',
            'events.0.epoch': f'u64:{p.epoch}', 'events.0.sequence': 'u64:1',
            'events.0.revision': 'u64:1', 'events.0.configuration': 'u64:0',
            'events.0.kind': 'enum:transition',
        })
        self.assertEqual(p.handle(poll), response)
        self.fields(p.handle(self.request(p, 5, 'cancel')))
        fields = self.fields(p.handle(self.request(p, 6, 'events', ('u64:1',))))
        self.assertEqual((fields['events.0.sequence'], fields['events.0.kind']),
                         ('u64:3', 'enum:cancelled'))
        self.assertEqual(model.calls.count(('step', 0, 1)), 1)

    def test_syntax_errors_have_zero_id_without_retiring_request(self):
        cases = ((1, 'status', ('extra',), 'malformed'),
                 (1, 'checks', ('u64:1', 'extra'), 'malformed'),
                 (1, 'step', ('bytes:0g',), 'malformed'),
                 (1, 'step', (), 'malformed'),
                 (1, 'unknown', (), 'unsupported'),
                 (2, 'status', (), 'unsupported_version'))
        for version, command, args, error in cases:
            with self.subTest(version=version, command=command, args=args):
                model, p = self.make()
                invalid = replace(self.request(p, 1, command, args), version=version)
                responses = self.exchange(p, [encode_request(invalid), encode_request(self.request(p, 1))])
                self.assertEqual((responses[0].request_id, responses[0].error, responses[0].fields), (0, error, ()))
                self.assertIsNone(responses[1].error)
                self.assertEqual(model.calls, [('initial',), ('state', 0)])

    def test_utf8_field_and_count_errors_follow_rust_decode_order(self):
        for payload, error in ((b'%' + b'\tx' * 64, 'malformed'),
                               (b'x' * 8193 + b'\t\xff', 'malformed'),
                               (b'%' + b'x' * 24576, 'limit'),
                               (b'\t'.join([b'x'] * 65), 'limit')):
            with self.subTest(error=error, prefix=payload[:5]):
                model, p = self.make()
                responses = self.exchange(p, [payload, encode_request(self.request(p, 2))])
                self.assertEqual((responses[0].request_id, responses[0].error), (0, error))
                self.assertIsNone(responses[1].error)
                self.assertEqual(model.calls, [('initial',), ('state', 0)])

    def test_full_width_ids_typed_duplicates_and_eviction(self):
        model, p = self.make(cache=1)
        large = (1 << 64) - 2
        request = self.request(p, large, 'step', ('bytes:01',))
        first = p.handle(request)
        self.assertEqual(p.handle(request), first)
        self.assertEqual(p.handle(replace(request, arguments=('bytes:02',))).error, 'duplicate_conflict')
        self.assertIsNone(p.handle(self.request(p, large + 1)).error)
        self.assertEqual(p.handle(request).error, 'retired_request')
        self.assertEqual(model.calls.count(('step', 0, 1)), 1)
        _, p = self.make()
        request = self.request(p, 1, 'checks', ('u64:0001',))
        canonical = replace(request, arguments=('u64:1',))
        self.assertEqual(decode_request(encode_request(request)), canonical)
        maximum = (1 << 64) - 1
        request = self.request(p, maximum, revision=maximum)
        self.assertEqual(decode_request(encode_request(request)), request)


if __name__ == '__main__':
    unittest.main()
