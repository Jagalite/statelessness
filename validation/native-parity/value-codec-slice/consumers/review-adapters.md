# Installed value adapters: static review

Read-only review of the frozen installed-consumer helpers below. This review ran
no application, build, test or installed-package command. Runtime receipts are
archived separately by the consumer owner; this document makes no runtime pass
claim or core codec qualification claim.

## Exact reviewed inputs

| Input | SHA-256 |
| --- | --- |
| `examples/native-values/contract.json` | `2f8b41e65715f0f57c43195ca0d38dde9459912c12a4c6db6b29d1f4b3d6dccc` |
| `examples/native-values/python/values.py` | `8b103d76556445987fd8996ff1f94d96fcb4df633304a535dfbe5cc497203e0d` |
| `examples/native-values/typescript/values.ts` | `a8cc88fb1e26a9af2502f41165a38f9c8a81b237efde522e76d7226c40949d27` |
| `examples/native-values/go/values.go` | `5b62a1039792cca3b434e10a1d463062df98b75641f3b9aa84e2581cf305430f` |
| `examples/native-values/swift/Sources/ValueConsumer/Values.swift` | `d3a19b3c1d6d2383e199864b75bbe36e4d670b30411746b5bdb3730ee496fe73` |

## Coverage and resolved findings

The contract contains **83 value rows and 37 distinct root schema declarations**.
Every root declaration exactly matches an entry in the contract's 41-row measured
layout registry. Recursively walking field/element/model/oracle declarations
found only two compound schema shapes absent from that registry:

- `Option<u8>` occurs in field 3 of `tuple-four-small-witness`.
- `Option<u64>` is the element in `vector-options-u64` and
  `vector-option-storage-exact`.

Both have exact separately measured named entries in the same contract:
`Option<u8>` size 2/alignment 1, and `Option<u64>` size 16/alignment 8. No generic
option/tag-plus-payload arithmetic is needed or approved by this review.

Python and Swift originally missed the nested named entries because their helper
lookup used only exact top-level registry rows. Their current lookups first use
the registry, then admit only the finite measured Option unit/bool/u8/u64 names
with an exact elementary child shape (Python dictionary equality; Swift one-key
child plus recognized kind). Those fixes cover the two recursive gaps without
inventing layouts for arbitrary compounds.

Go's scalar helper erases typed codecs through `NewValueCodec`, retaining the
scalar's explicit layout but losing the private built-in identity used by
`OptionOf`'s finite default layout table. Therefore a nested option around an
erased u64 cannot rely on that table. The reviewed Go helper now decodes the
contract's `Named` measurements and uses an exact elementary child-shape check
before applying the same finite Option names. Its existing explicit `[u16;3]`
size 6 handles the nested array collection case.

TypeScript uses the original built-in scalar descriptors rather than recreating
them during value adaptation. Its private built-in identities therefore survive,
and its finite measured Option table supplies the nested u8/u64 weights. Other
compound collection weights come from exact registry matches or the existing
finite known array/header entries.

No current contract shape loses a required measured-layout match because of a
`length_width` override: all 37 root shapes match exactly, and the recursively
unregistered shapes are the two options above. This conclusion is scoped to the
closed contract, not arbitrary future width/type declarations.

The helpers' direct-length paths were also inspected. Python and TypeScript
explicitly invoke raw length decode without an extra ordinary value guard. Go
uses the public direct string/vector length functions. Swift's selected-width
codec is decoded through its raw decode path for a direct-length row; erasure
forwards the underlying raw decode. Guarded rows enter through the reader's value
guard. No extra enclosing field/value guard or direct-length callback capability
gap was identified in these reviewed helper paths.

**Disposition:** no unresolved finding in this finite installed-adapter static
scope after the named-layout fixes. This is independent of subsequent installed
execution, cross-language wire results, source staging and package receipts.
