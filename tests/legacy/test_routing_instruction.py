"""System One requests with no record evidence (routing) classify the request text; with evidence, the chart instruction.
Codex review 2026-10-23: the chart instruction's "choose unknown when evidence is absent" made every routing call lean unknown."""
import unittest

from clinical_core.providers import INSTRUCTION, REQUEST_INSTRUCTION, ClaudeProvider, EvidenceTask, JevProvider

OPTIONS = {'ed_visits': 'Emergency department visits', 'unknown': 'None fits'}


class RoutingInstruction(unittest.TestCase):
    def test_no_evidence_classifies_the_request(self):
        task = EvidenceTask('How many ED visits last year?', '2030-05-27', OPTIONS, [])
        self.assertEqual(JevProvider(None).request(task)['questions']['reason']['instructions'], REQUEST_INSTRUCTION)
        self.assertEqual(ClaudeProvider(None).request(task)['system'], REQUEST_INSTRUCTION)

    def test_evidence_keeps_the_chart_instruction(self):
        task = EvidenceTask('P-1', '2030-05-27', OPTIONS, [{'id': 'n1', 'text': 'ED visit', 'date': '2030-01-01', 'note_id': 'n1'}])
        self.assertEqual(JevProvider(None).request(task)['questions']['reason']['instructions'], INSTRUCTION)


if __name__ == '__main__':
    unittest.main()
