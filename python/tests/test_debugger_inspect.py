"""Manual structural projection, omission and conservative comparison goldens."""
import unittest
from dataclasses import replace
from statelessness import ModelError
from statelessness.debugger_inspect import (SnapshotId, DisplaySchema, PathSegment, InspectLimits,
    PageRequest, InspectQuery, InspectNode, FieldView, ObjectView, EnumView, inspect,
    compare_nodes, diff, InspectError)


class InspectTests(unittest.TestCase):
    def test_payload_sizing_counts_multibyte_values_across_chunks(self):
        from statelessness.debugger_inspect import _payload_bytes
        value='a'*4095+'😀'+'é'*4096
        self.assertEqual(_payload_bytes(value), 4095+4+8192)

    def test_large_projection_comparison_uses_explicit_budget(self):
        value='x'*(256*1024+1)
        limits=InspectLimits(max_work=300_000,max_bytes=300_000,max_value_bytes=300_000)
        result=inspect(value,InspectQuery(limits=limits))
        self.assertTrue(result.node.complete_with_budget(300_000))
        self.assertEqual(diff(result,result,max_chars=300_000).kind,'unchanged')
        with self.assertRaises(InspectError): diff(result,result)

    def test_custom_string_projection_reports_invalid_unicode(self):
        class Adapter:
            def inspect(self,path,context):
                return InspectNode('string','\ud800',total_bytes=1)
        with self.assertRaisesRegex(InspectError,'string must be UTF-8'):
            inspect(Adapter())

    @unittest.skipUnless(__import__('os').environ.get('STATELESS_RUST_INSPECTION_BUDGET'), 'requires fresh Rust budget fixture')
    def test_rust_node_and_depth_budget_differential(self):
        import os
        from pathlib import Path
        from statelessness.debugger_inspect import IntegerView
        cases=(('nodes1',1,32),('nodes2',2,32),('nodes3',3,32),('depth0',10,0),('depth1',10,1))
        output=[]
        for name,nodes,depth in cases:
            values=(IntegerView('u8','1'),IntegerView('u8','2'))
            if name=='depth1': values=(values,)
            node=inspect(values,InspectQuery(limits=InspectLimits(max_nodes=nodes,max_depth=depth))).node
            reason={'complete':'Complete','nodes':'Partial(Nodes)','depth':'Partial(Depth)'}[node.completeness]
            page=node.page
            output.append(f'{name}\t{reason}\t{page.returned}\t{page.total}\t{page.next_offset if page.next_offset is not None else "none"}')
        self.assertEqual(output,Path(os.environ['STATELESS_RUST_INSPECTION_BUDGET']).read_text().splitlines())

    def test_adapter_errors_preserve_typed_errors_without_formatting_host_exceptions(self):
        class Adapter:
            def __init__(self, error): self.error = error
            def inspect(self, path, context): raise self.error
        class HostError(Exception):
            def __str__(self): raise AssertionError("host exception was formatted")
        with self.assertRaisesRegex(InspectError, '^path not found$'):
            inspect(Adapter(InspectError('path not found')))
        with self.assertRaisesRegex(InspectError, '^invalid adapter$'):
            inspect(Adapter(HostError()))
        class SchemaAdapter:
            def schema(self): raise HostError()
            def inspect(self, path, context): raise AssertionError("projection evaluated")
        with self.assertRaisesRegex(InspectError, '^schema callback failed$'):
            inspect(SchemaAdapter())

    def test_structural_full_width_and_stable_field_identity(self):
        value=ObjectView('Counter',(FieldView('n','Count',(1<<64)-1), FieldView('name','Name','hello')))
        result=inspect(value)
        self.assertTrue(result.node.is_complete)
        leaf=inspect(value,InspectQuery(path=(PathSegment('field','n'),)))
        self.assertEqual(leaf.node.value,('python-int','18446744073709551615'))
        self.assertEqual(result.node.children[0].segment,PathSegment('field','n'))
        self.assertEqual(compare_nodes(result.node,result.node),'unchanged')

    def test_redacted_skips_projection_and_is_unknown_before_equality(self):
        class Secret:
            def inspect(self,*args): raise AssertionError('secret evaluated')
        result=inspect(ObjectView('Secret',(FieldView('secret','Secret',Secret(),hidden=True),)))
        self.assertEqual(result.node.children[0].node.kind,'redacted')
        self.assertEqual(compare_nodes(result.node,result.node),'unknown')
        self.assertEqual(compare_nodes(None,result.node),'unknown')

    def test_pagination_depth_bytes_and_work(self):
        result=inspect((1,2,3),InspectQuery(page=PageRequest(1,1)))
        self.assertEqual(result.node.page.next_offset,2)
        self.assertFalse(result.node.is_complete)
        self.assertEqual(result.node.children[0].segment.value,1)
        preview=inspect('éx',InspectQuery(limits=InspectLimits(max_value_bytes=1)))
        self.assertEqual(preview.node.value,'')
        self.assertEqual(preview.node.total_bytes,3)
        self.assertEqual(compare_nodes(preview.node,preview.node),'unknown')
        nested=inspect(((1,),),InspectQuery(limits=InspectLimits(max_depth=0)))
        self.assertEqual(nested.node.completeness,'depth')
        self.assertEqual(nested.node.children,())
        partial=inspect((1,2),InspectQuery(limits=InspectLimits(max_work=1)))
        self.assertEqual((partial.node.completeness,partial.node.page.returned,partial.node.page.next_offset),('work',0,0))
        partial=inspect((1,2),InspectQuery(limits=InspectLimits(max_nodes=1)))
        self.assertEqual((partial.node.completeness,partial.node.page.returned,partial.node.page.next_offset),('nodes',0,0))
        partial=inspect((1,2),InspectQuery(limits=InspectLimits(max_bytes=450)))
        self.assertEqual((partial.node.completeness,partial.node.page.returned,partial.node.page.next_offset),('bytes',0,0))
        with self.assertRaises(ModelError): inspect('hello',InspectQuery(limits=InspectLimits(max_bytes=1)))

    def test_enum_paths_and_unstable_maps(self):
        value=EnumView('State','ready','Ready',(FieldView('x','X',1),))
        result=inspect(value,InspectQuery(path=(PathSegment('variant','ready'),PathSegment('field','x'))))
        self.assertEqual(result.node.value,('python-int','1'))
        class Map:
            def schema(self): return DisplaySchema('Map')
            def inspect(self,path,cx): return cx.map(path,((('u64','1'),'value'),),ordering='unstable')
        projected=inspect(Map())
        self.assertFalse(projected.node.is_complete)
        self.assertEqual(compare_nodes(projected.node,projected.node),'unknown')
        with self.assertRaises(ModelError): inspect(value,InspectQuery(path=(PathSegment('variant','other'),)))

    def test_schema_diff_and_custom_adapter_output_admission(self):
        before=inspect(1)
        after=replace(before,schema=DisplaySchema('other'))
        self.assertEqual(diff(before,after).kind,'unknown')
        class Bypass:
            def inspect(self,path,cx): return InspectNode('bytes',b'x'*4096,total_bytes=4096)
        with self.assertRaises(ModelError): inspect(Bypass(),InspectQuery(limits=InspectLimits(max_bytes=128)))
        spoof=InspectNode('bytes',b'x',total_bytes=10)
        self.assertFalse(spoof.is_complete)

    def test_duplicate_child_paths_make_comparison_unknown(self):
        from statelessness.debugger_inspect import ChildNode
        leaf=InspectNode('scalar',('u64','1'))
        a=ChildNode(PathSegment('field','same'),'first',leaf)
        b=ChildNode(PathSegment('field','same'),'second',leaf)
        duplicate=InspectNode('object','item',(a,b),2)
        distinct=InspectNode('object','item',(a,replace(b,segment=PathSegment('field','other'))),2)
        self.assertFalse(duplicate.is_complete)
        self.assertEqual(compare_nodes(duplicate,duplicate),'unknown')
        self.assertEqual(compare_nodes(None,duplicate),'unknown')
        self.assertTrue(distinct.is_complete)
        self.assertEqual(compare_nodes(distinct,distinct),'unchanged')
        nested=InspectNode('sequence','items',(ChildNode(PathSegment('index',0),'item',duplicate),),1)
        self.assertFalse(nested.is_complete)
        self.assertEqual(compare_nodes(nested,nested),'unknown')

    def test_selected_duplicate_paths_fail_before_projection_and_obey_work_budget(self):
        calls=[]
        class Leaf:
            def inspect(self,path,cx):
                calls.append('projected')
                return cx.scalar(path,('u64','1'))
        class Mapping:
            def __init__(self,entries): self.entries=entries
            def inspect(self,path,cx): return cx.map(path,self.entries)
        cases=((ObjectView('item',(FieldView('x','first',Leaf()),FieldView('x','second',Leaf()))),PathSegment('field','x')),
               (Mapping((('x',Leaf()),('x',Leaf()))),PathSegment('map-key','x')))
        for value,path in cases:
            with self.subTest(path=path):
                with self.assertRaisesRegex(InspectError,'invalid metadata'):
                    inspect(value,InspectQuery(path=(path,)))
                self.assertEqual(calls,[])
                with self.assertRaisesRegex(InspectError,'work limit'):
                    inspect(value,InspectQuery(path=(path,),limits=InspectLimits(max_work=1)))
                self.assertEqual(calls,[])
        unique=ObjectView('item',(FieldView('x','first',Leaf()),FieldView('y','other',Leaf())))
        self.assertEqual(inspect(unique,InspectQuery(path=(PathSegment('field','x'),))).node.value,('u64','1'))
        self.assertEqual(calls,['projected'])

    def test_returned_nodes_cannot_bypass_per_value_budget(self):
        class Bypass:
            def __init__(self,node): self.node=node
            def inspect(self,path,cx): return self.node
        cases=(InspectNode('string','éé',total_bytes=4),
               InspectNode('bytes',b'abc',total_bytes=3),
               InspectNode('scalar',('u64','123')),
               InspectNode('scalar',('f32','123',0)))
        for node in cases:
            with self.subTest(kind=node.kind,value=node.value):
                with self.assertRaisesRegex(InspectError,'value limit'):
                    inspect(Bypass(node),InspectQuery(limits=InspectLimits(max_value_bytes=2)))
        self.assertEqual(inspect(Bypass(InspectNode('scalar',('u64','12'))),
            InspectQuery(limits=InspectLimits(max_value_bytes=2))).node.value,('u64','12'))

    def test_returned_tree_cannot_bypass_work_budget(self):
        from statelessness.debugger_inspect import ChildNode
        child=ChildNode(PathSegment('index',0),'item',InspectNode('scalar',('u64','1')))
        class Bypass:
            def inspect(self,path,cx):
                return InspectNode('sequence','items',(child,),1)
        with self.assertRaisesRegex(InspectError,'work limit'):
            inspect(Bypass(),InspectQuery(limits=InspectLimits(max_work=2)))
        result=inspect(Bypass(),InspectQuery(limits=InspectLimits(max_work=3)))
        self.assertEqual(result.node.children[0].node.value,('u64','1'))

    def test_custom_nodes_reject_unrepresentable_wire_variants(self):
        class Bypass:
            def __init__(self,node): self.node=node
            def inspect(self,path,cx): return self.node
        cases=(InspectNode('scalar',('u64','1'),completeness='invented'),
               InspectNode('truncated'),InspectNode('map',ordering='invented'),
               InspectNode('string','hello'),InspectNode('bytes',b'hello'),
               InspectNode('scalar',('u64','1'),page='invented'))
        for node in cases:
            with self.subTest(node=node):
                with self.assertRaises(InspectError): inspect(Bypass(node))
        valid=InspectNode('truncated',completeness='observation-gap')
        self.assertEqual(inspect(Bypass(valid)).node,valid)

    def test_typed_scalar_and_map_identity_admission(self):
        from statelessness.debugger_inspect import IntegerView, CharView, FloatView
        node=inspect(IntegerView('u64','18446744073709551615')).node
        self.assertEqual(node.value,('u64','18446744073709551615'))
        self.assertEqual(inspect(IntegerView('u8','256')).node.value,('u8','256'))
        self.assertEqual(inspect(IntegerView('u8','label')).node.value,('u8','label'))
        self.assertEqual(PathSegment('map-key',('u64','-1')).value,('u64','-1'))
        with self.assertRaises(ValueError): CharView('\ud800')
        self.assertEqual(inspect(FloatView('f32','1',1065353216)).node.value,('f32','1',1065353216))
        self.assertEqual(inspect(FloatView('f32','text',(1<<64)-1)).node.value,('f32','text',(1<<64)-1))
        self.assertEqual(inspect(FloatView('u8','arbitrary',7)).node.value,('float','u8','arbitrary',7))
        self.assertNotEqual(inspect(FloatView('u8','arbitrary',7)).node.value,inspect(IntegerView('u8','arbitrary')).node.value)
        class Alias:
            def __init__(self,kind): self.kind=kind
            def inspect(self,path,cx): return InspectNode('scalar',('float',self.kind,'1',0))
        for kind in ('f32','f64'):
            with self.subTest(alias=kind), self.assertRaises(InspectError): inspect(Alias(kind))

    def test_local_integer_map_keys_preserve_arbitrary_text(self):
        class Mapping:
            def inspect(self,path,cx):
                return cx.map(path,((('u8','256'), 7), (('u8','label'), 8)))
        value=Mapping()
        projected=inspect(value).node
        self.assertEqual([child.segment.value for child in projected.children], [('u8','256'),('u8','label')])
        selected=inspect(value,InspectQuery(path=(PathSegment('map-key',('u8','label')),)))
        self.assertEqual(selected.node.value,('python-int','8'))
        with self.assertRaises(ModelError): PathSegment('map-key',('bogus','1'))
        with self.assertRaises(ModelError): PathSegment('map-key',('u8','\ud800'))

    def test_builtin_f64_projection_matches_rust_display_examples(self):
        cases=((0.1,'0.1'),(1.0,'1'),(-0.0,'-0'),(1e20,'100000000000000000000'),
               (1e-7,'0.0000001'),(float('inf'),'inf'),(float('-inf'),'-inf'),(float('nan'),'NaN'))
        import struct
        for value,decimal in cases:
            with self.subTest(decimal=decimal):
                bits=int.from_bytes(struct.pack('>d',value),'big')
                self.assertEqual(inspect(value).node.value,('f64',decimal,bits))

    def test_builtin_integer_value_limit_preserves_exact_fitting_values(self):
        for value,limit in ((1000,4),(9999,4),(-999,4),(0,1)):
            with self.subTest(value=value):
                node=inspect(value,InspectQuery(limits=InspectLimits(max_value_bytes=limit))).node
                self.assertEqual(node.value,('python-int',str(value)))
        for value,limit in ((10000,4),(-1000,4),(9,0),(1<<100000,10)):
            with self.subTest(value=value.bit_length(),limit=limit):
                node=inspect(value,InspectQuery(limits=InspectLimits(max_value_bytes=limit))).node
                self.assertEqual((node.kind,node.completeness),('truncated','value-limit'))

    def test_builtin_scan_work_and_schema_payload_are_bounded(self):
        with self.assertRaises(ModelError): inspect('a'*1000,InspectQuery(limits=InspectLimits(max_work=10)))
        class Schema:
            def schema(self): return DisplaySchema('x'*5000)
            def inspect(self,path,cx): return cx.scalar(path,('unit',None))
        with self.assertRaises(ModelError): inspect(Schema())
        value=inspect(1<<100000,InspectQuery(limits=InspectLimits(max_value_bytes=10)))
        self.assertFalse(value.node.is_complete)

    def test_utf8_metadata_bytes_and_opaque_preview(self):
        class WideSchema:
            def schema(self): return DisplaySchema('é'*2049)
            def inspect(self,path,cx): return cx.scalar(path,('unit',None))
        with self.assertRaisesRegex(ModelError,'metadata value limit'): inspect(WideSchema())
        class InvalidSchema:
            def schema(self): return DisplaySchema('\ud800')
            def inspect(self,path,cx): return cx.scalar(path,('unit',None))
        with self.assertRaisesRegex(ModelError,'UTF-8'): inspect(InvalidSchema())
        class OversizedInvalidSchema:
            def schema(self): return DisplaySchema('x'*1000000+'\ud800')
            def inspect(self,path,cx): return cx.scalar(path,('unit',None))
        # The size bound wins without encoding a potentially unbounded string.
        with self.assertRaisesRegex(ModelError,'metadata value limit'): inspect(OversizedInvalidSchema())
        class Opaque:
            def inspect(self,path,cx): return cx.opaque(path,'éx')
        node=inspect(Opaque(),InspectQuery(limits=InspectLimits(max_value_bytes=1))).node
        self.assertEqual(node.value,'')
        self.assertFalse(node.is_complete)
        node=inspect(Opaque(),InspectQuery(limits=InspectLimits(max_value_bytes=2))).node
        self.assertEqual(node.value,'é')

    def test_opaque_label_scan_obeys_work_budget(self):
        class Opaque:
            def inspect(self,path,cx): return cx.opaque(path,'a'*10000)
        with self.assertRaisesRegex(ModelError,'work limit'):
            inspect(Opaque(),InspectQuery(limits=InspectLimits(max_work=10,max_value_bytes=1)))
        class InvalidSuffix:
            def inspect(self,path,cx): return cx.opaque(path,'abc\ud800')
        with self.assertRaisesRegex(ModelError,'metadata must be UTF-8'):
            inspect(InvalidSuffix(),InspectQuery(limits=InspectLimits(max_work=10,max_value_bytes=1)))
        with self.assertRaisesRegex(ModelError,'work limit'):
            inspect(InvalidSuffix(),InspectQuery(limits=InspectLimits(max_work=3,max_value_bytes=1)))

    def test_opaque_preview_uses_value_budget_not_metadata_limit(self):
        class Opaque:
            def inspect(self,path,cx): return cx.opaque(path,'é'*2500)
        limits=InspectLimits(max_work=6000,max_value_bytes=5000)
        self.assertEqual(inspect(Opaque(),InspectQuery(limits=limits)).node.value,'é'*2500)
        class Bypass:
            def inspect(self,path,cx): return InspectNode('opaque','x'*5001,completeness='opaque')
        with self.assertRaisesRegex(InspectError,'projection value limit'):
            inspect(Bypass(),InspectQuery(limits=limits))

    def test_invalid_unicode_string_reports_inspection_error(self):
        # Python strings can contain unpaired surrogates, unlike Rust str.
        # Inspect the whole value even when the invalid suffix is not retained.
        with self.assertRaisesRegex(InspectError, 'string must be UTF-8'):
            inspect('abc\ud800', InspectQuery(limits=InspectLimits(max_value_bytes=1)))
        with self.assertRaisesRegex(InspectError, 'work limit'):
            inspect('abc\ud800', InspectQuery(limits=InspectLimits(max_value_bytes=1,max_work=3)))
