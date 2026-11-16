"""Candidate AGERE_NATIVE_ROUTING: the analyst is told that follow-up facts belong to the loop_closure engine."""
import os,unittest
from unittest import mock
from clinical_core.agere.clinical import backstop_agent as B


class NativeRouting(unittest.TestCase):
    def test_off_leaves_the_tool_list_untouched(self):
        with mock.patch.dict(os.environ,{'AGERE_NATIVE_ROUTING':'off'}):self.assertIs(B.routed_tools(),B.TOOLS)

    def test_on_adds_routing_to_named_tools_only_and_keeps_schemas(self):
        with mock.patch.dict(os.environ,{'AGERE_NATIVE_ROUTING':'on'}):tools=B.routed_tools()
        self.assertEqual([t['name']for t in tools],[t['name']for t in B.TOOLS])
        for new,old in zip(tools,B.TOOLS):
            self.assertEqual(new['input_schema'],old['input_schema'])
            if old['name']in B.NATIVE_ROUTING:
                self.assertTrue(new['description'].startswith(old['description']));self.assertIn('loop_closure',new['description'])
            else:self.assertEqual(new,old)
        self.assertEqual(set(B.NATIVE_ROUTING)-{t['name']for t in B.TOOLS},set())


if __name__=='__main__':unittest.main()
