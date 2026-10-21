"""Independent scenario oracle: stdlib only; never imports an application codec.

Numbers, check phases, tags and model payloads below are the scenario contract,
not goldens obtained by running an engine. Rust's build stamp must come from an
independently pinned build identity, never from the artifact under comparison.
"""
import os
import re
import zlib

MAGIC = b'STLESS\x1a\n'
CAPTURE_COUNTS = dict(initial=0, reducer=3, state=4, transition=3,
    encode_state=14, encode_input=6, encode_output=6,
    decode_state=0, decode_input=0, limits=0)
REPLAY_COUNTS = dict(initial=0, reducer=3, state=4, transition=3,
    encode_state=4, encode_input=3, encode_output=3,
    decode_state=1, decode_input=3, limits=4)


def uint(value, width):
    return value.to_bytes(width, 'little')


def blob(value):
    return uint(len(value), 4) + value


def text(value):
    return blob(value.encode('utf-8'))


def frame(tag, body):
    record = uint(tag, 1) + uint(len(body), 4) + body
    return record + uint(zlib.crc32(record), 4)


def checks(sequence):
    # Initial and each post-state have one state check. Turns add one edge check.
    failed = sequence == 3
    state = text('threshold.bound') + uint(int(failed), 1)
    if failed:
        state += text('at least three')
    edge = text('edge.checked') + b'\0' if sequence else b''
    return uint(2 if sequence else 1, 4) + state + edge


def parameters(engine, retained, rust_build):
    if engine == 'rust':
        build = rust_build if rust_build is not None else os.environ.get('N13_EXPECTED_RUST_BUILD')
        if build is None or re.fullmatch(r'source-fnv1a64:[0-9a-f]{16}', build) is None:
            raise ValueError('provide independently pinned Rust build identity via rust_build or N13_EXPECTED_RUST_BUILD')
        version = '0.2.0'
    elif engine in ('python', 'typescript', 'go', 'swift'):
        build, version = 'native-' + engine + '-v1', '0.1.0'
    else:
        raise ValueError('unknown engine: ' + engine)
    return [('region', 'test'), ('stateless.engine.version', version),
        ('stateless.engine.build', build)] + [('stateless.monitor.' + key, value) for key, value in (
        ('max_steps', '3'), ('max_retained_bytes', str(retained)), ('checking', 'full'),
        ('observed_steps', '3'), ('evicted_steps', '0'), ('checkpoint_after_sequence', '0'))]


def trace(engine, retained, rust_build):
    pairs = parameters(engine, retained, rust_build)
    # ModelMetadata: name, model/properties/codec version, build.
    run = text('n13-installed-model') + uint(1, 4) * 3 + text('application-v1')
    # RunConfig: strategy, Some(seed), ordered parameter pairs.
    run += text('manual') + b'\1' + uint(7, 8) + uint(len(pairs), 4)
    run += b''.join(text(k) + text(v) for k, v in pairs)
    run += blob(uint(0, 4)) + checks(0)
    result = MAGIC + b'\1\0\0\0' + frame(1, run)
    for sequence in (1, 2, 3):
        # Frame indices are zero-based. Application Input is Add(tag1, U8(1)).
        body = uint(sequence - 1, 8) + blob(b'\1\1') + b'\0'
        # One encode-only Output and the post State both contain LE U32 count.
        body += uint(1, 4) + blob(uint(sequence, 4)) + blob(uint(sequence, 4))
        result += frame(2, body + checks(sequence))
    # Three steps, termination variant property_failed=1.
    return result + frame(3, uint(3, 8) + b'\1')


def evidence_metadata():
    # checkpoint_after, Provenance, then three StepEvidence records.
    result = uint(0, 8) + text('n13-installed-run') + uint(7, 8)
    result += b'\1' + text('manifest-v1') + b'\1' + text('consumer-source')
    result += uint(1, 4) + text('region') + text('test') + uint(3, 4)
    for sequence in (1, 2, 3):
        result += uint(sequence, 8) + uint(1, 4)  # one state-phase check
        result += b'\1' + text('worker-a') + b'\1' + uint(9, 8)
        result += b'\1' + text('request-1') + b'\1' + uint(10 + sequence, 8)
        result += uint(0 if sequence == 1 else 1, 4)
        if sequence != 1:
            result += uint(sequence - 1, 8) + uint(0, 4)
    return result


def expected(engine, *, rust_build=None):
    """Return literal-scenario v1/v2 hex; retain each producer's engine stamps."""
    v1 = trace(engine, 16384, rust_build)
    # Evidence reserves max_metadata(1024) plus wrapper overhead(32).
    inner = trace(engine, 15328, rust_build)
    metadata = evidence_metadata()
    v2 = MAGIC + b'\2\0\0\0' + uint(len(inner), 8) + uint(len(metadata), 8)
    v2 += inner + metadata
    v2 += uint(zlib.crc32(v2), 4)
    return dict(v1=v1.hex(), v2=v2.hex())


def rust_build_id(source, rustc_version, target, flags=''):
    """Reproduce build.rs from staged source plus independently captured inputs.

    rustc_version is the exact stdout bytes (including final newline) of the
    compiler selected for the install. This helper assumes cargo install's
    release profile: PROFILE=release OPT_LEVEL=3 DEBUG=false. Pass flags as the
    exact CARGO_ENCODED_RUSTFLAGS value, not shell RUSTFLAGS spelling.
    """
    from pathlib import Path
    root = Path(source)
    files = [Path('Cargo.toml'), Path('build.rs'),
        Path('docs/requirements/controller-properties.txt'), Path('docs/requirements/controller.json')]
    files += [path.relative_to(root) for path in (root / 'src').rglob('*') if path.is_file()]
    value = 0xcbf29ce484222325
    def add(data):
        nonlocal value
        for byte in uint(len(data), 8) + data:
            value = ((value ^ byte) * 0x100000001b3) & ((1 << 64) - 1)
    for relative in sorted(files, key=lambda path: path.as_posix().encode()):
        add(relative.as_posix().encode())
        add((root / relative).read_bytes())
    if not isinstance(rustc_version, bytes):
        raise TypeError('rustc_version must be exact captured stdout bytes')
    add(rustc_version)
    for key, entry in [('TARGET', target), ('PROFILE', 'release'), ('OPT_LEVEL', '3'),
                       ('DEBUG', 'false'), ('CARGO_ENCODED_RUSTFLAGS', flags)]:
        add(key.encode()); add(entry.encode())
    return f'source-fnv1a64:{value:016x}'
