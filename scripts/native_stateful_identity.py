"""Verify a caller-pinned cross-engine identity manifest before strict replay.

The manifest is an attestation, not proof that an arbitrary model object was
built from the supplied artifacts. The caller must bind the running consumer to
those artifacts and independently review the mapping. Strict replay never
permits build mismatch, including when this manifest verifies.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path

from statelessness.core import replay


class IdentityError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IdentityError('duplicate manifest key')
        result[key] = value
    return result


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _hash_file(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class _PinnedMetadataModel:
    """Revalidate the live model at replay admission before decoding anything."""
    def __init__(self, model, expected):
        self._model, self._expected = model, expected

    def metadata(self):
        actual = self._model.metadata()
        fields = ('name', 'model_version', 'properties_version', 'codec_version', 'build')
        if any(getattr(actual, key, None) != getattr(self._expected, key) for key in fields):
            raise IdentityError('consumer metadata changed during replay')
        return actual

    def __getattr__(self, name):
        return getattr(self._model, name)


@dataclass(frozen=True)
class VerifiedStrictReplay:
    manifest_sha256: str
    producer_build: str
    consumer_build: str
    report: object
    semantic_identity: tuple


@dataclass(frozen=True)
class CrossBuildObservation:
    """A caller-attested cross-build observation comparison, not exact-build replay."""
    manifest_sha256: str
    producer_build: str
    consumer_build: str
    strict_report: object
    observation_report: object


def verify_and_replay(model, trace, manifest_bytes, expected_manifest_sha256, files):
    """Verify exact attested identities and files, then run ordinary strict replay.

    ``files`` maps the manifest's opaque file identifiers to caller-supplied
    paths. Both sides must identify at least one source and one artifact file.
    The returned report may be incompatible: a verified mapping does not make
    different builds equal and does not claim cross-build replay succeeded.
    """
    if type(manifest_bytes) is not bytes or len(manifest_bytes) > 1024 * 1024:
        raise IdentityError('invalid manifest size')
    if not _digest(expected_manifest_sha256) or sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise IdentityError('manifest digest mismatch')
    try:
        data = json.loads(manifest_bytes.decode('utf8'), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IdentityError('invalid manifest JSON') from error
    if type(data) is not dict or set(data) != {'version', 'semantic', 'producer', 'consumer'} or type(data['version']) is not int or data['version'] != 1:
        raise IdentityError('invalid manifest shape')
    semantic = data['semantic']
    fields = ('name', 'model_version', 'properties_version', 'codec_version')
    if type(semantic) is not dict or set(semantic) != set(fields) or type(semantic['name']) is not str or any(type(semantic[k]) is not int or not 0 <= semantic[k] <= 0xffffffff for k in fields[1:]):
        raise IdentityError('invalid semantic identity')
    if type(files) is not dict:
        raise IdentityError('invalid file mapping')
    builds = []
    used = set()
    for side in ('producer', 'consumer'):
        entry = data[side]
        if type(entry) is not dict or set(entry) != {'build', 'source', 'artifact'} or type(entry['build']) is not str or not entry['build']:
            raise IdentityError('invalid side identity')
        builds.append(entry['build'])
        for kind in ('source', 'artifact'):
            group = entry[kind]
            if type(group) is not dict or not group:
                raise IdentityError('missing source or artifact hashes')
            for key, digest in group.items():
                if type(key) is not str or not key or not _digest(digest) or key not in files:
                    raise IdentityError('invalid file identity')
                try:
                    actual = _hash_file(files[key])
                except (OSError, TypeError) as error:
                    raise IdentityError('cannot read identity file') from error
                if actual != digest:
                    raise IdentityError('file digest mismatch')
                used.add(key)
    if set(files) != used:
        raise IdentityError('unused file mapping')
    producer = trace.metadata
    consumer = model.metadata()
    if any(getattr(producer, k) != semantic[k] or getattr(consumer, k) != semantic[k] for k in fields):
        raise IdentityError('semantic identity mismatch')
    if producer.build != builds[0] or consumer.build != builds[1]:
        raise IdentityError('build identity mismatch')
    # Do not pass allow_build_mismatch. A source mapping is not build equality.
    report = replay(_PinnedMetadataModel(model, consumer), trace)
    return VerifiedStrictReplay(expected_manifest_sha256, *builds, report,
                                tuple(semantic[k] for k in fields))


def verify_and_compare_cross_build(model, trace, manifest_bytes, expected_manifest_sha256, files):
    """Compare observations across attested builds, preserving strict rejection.

    This explicit profile follows REPLAY-004: only build equality is waived for
    the second replay. It does not authenticate the manifest or prove that the
    supplied files produced the executing models. A caller must independently
    establish that provenance. The first replay remains strict and must reject
    the distinct builds; no trace metadata is rewritten.
    """
    strict = verify_and_replay(model, trace, manifest_bytes, expected_manifest_sha256, files)
    if (strict.producer_build == strict.consumer_build or strict.report.outcome != 'incompatible'
            or strict.report.build_matches):
        raise IdentityError('cross-build profile requires strict build incompatibility')
    from statelessness.core import Metadata
    expected = Metadata(*strict.semantic_identity, strict.consumer_build)
    fields = ('name', 'model_version', 'properties_version', 'codec_version')
    if (trace.metadata.build != strict.producer_build or
            tuple(getattr(trace.metadata, key) for key in fields) != strict.semantic_identity):
        raise IdentityError('producer metadata changed during comparison')
    report = replay(_PinnedMetadataModel(model, expected), trace, allow_build_mismatch=True)
    if report.build_matches:
        raise IdentityError('cross-build metadata changed during comparison')
    return CrossBuildObservation(strict.manifest_sha256, strict.producer_build,
                                 strict.consumer_build, strict.report, report)
