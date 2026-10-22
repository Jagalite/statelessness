"""Manual structural projection, omission and conservative comparison goldens."""
import unittest
from dataclasses import replace
from statelessness import ModelError
from statelessness.debugger_inspect import (SnapshotId, DisplaySchema, PathSegment, InspectLimits,
    PageRequest, InspectQuery, InspectNode, FieldView, ObjectView, EnumView, inspect,
    compare_nodes, diff)


class InspectTests(unittest.TestCase):
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
        self.assertEqual(nested.node.children[0].node.completeness,'depth')
        with self.assertRaises(ModelError): inspect((1,2),InspectQuery(limits=InspectLimits(max_work=1)))
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

    def test_typed_scalar_and_map_identity_admission(self):
        from statelessness.debugger_inspect import IntegerView, CharView, FloatView
        node=inspect(IntegerView('u64','18446744073709551615')).node
        self.assertEqual(node.value,('u64','18446744073709551615'))
        with self.assertRaises(ModelError): IntegerView('u8','256')
        with self.assertRaises(ModelError): PathSegment('map-key',('u64','-1'))
        with self.assertRaises(ValueError): CharView('\ud800')
        self.assertEqual(inspect(FloatView('f32','1',1065353216)).node.value,('f32','1',1065353216))

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
        class Opaque:
            def inspect(self,path,cx): return cx.opaque(path,'éx')
        node=inspect(Opaque(),InspectQuery(limits=InspectLimits(max_value_bytes=1))).node
        self.assertEqual(node.value,'')
        self.assertFalse(node.is_complete)
        node=inspect(Opaque(),InspectQuery(limits=InspectLimits(max_value_bytes=2))).node
        self.assertEqual(node.value,'é')
