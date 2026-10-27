"""Installed N13 finite producer/consumer; literal independent wire expectations."""
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statelessness import (ordered_record, field, tagged_enum, case, U32, STRING,
                          encode_canonical, decode_canonical)

@dataclass(frozen=True)
class Point:
    x: int
    y: str

POINT = ordered_record('Point', value_type=Point, fields=(
    field('x', U32, get=lambda p: p.x),
    field('y', STRING, get=lambda p: p.y)), construct=lambda xs: Point(*xs))
ENUM = tagged_enum('Choice', select=lambda v: v[0], cases=(
    case('Empty', tag=0, construct=lambda xs: ('Empty',)),
    case('Point', tag=1, fields=(field('payload', POINT, get=lambda v: v[1]),),
         construct=lambda xs: ('Point', xs[0]))))
EXPECTED = [dict(id='point', hex='07000000020000006869'),
            dict(id='empty', hex='00'),
            dict(id='variant', hex='0107000000020000006869')]
VALUES = {'point': Point(7, 'hi'), 'empty': ('Empty',), 'variant': ('Point', Point(7, 'hi'))}
SCHEMAS = {'point': POINT, 'empty': ENUM, 'variant': ENUM}

def main():
    mode = sys.argv[1]
    if mode == 'produce':
        rows = [dict(id=r['id'], hex=encode_canonical(SCHEMAS[r['id']], VALUES[r['id']]).hex()) for r in EXPECTED]
        destination = sys.argv[2]
    elif mode == 'transcode':
        rows = json.loads(Path(sys.argv[2]).read_text())
        destination = sys.argv[3]
        for row in rows:
            value = decode_canonical(SCHEMAS[row['id']], bytes.fromhex(row['hex']))
            assert value == VALUES[row['id']]
            row['hex'] = encode_canonical(SCHEMAS[row['id']], value).hex()
    else: raise ValueError('unknown mode')
    assert rows == EXPECTED
    Path(destination).write_text(json.dumps(rows))
    print(json.dumps({'case_count': len(rows)}))

if __name__ == '__main__': main()
