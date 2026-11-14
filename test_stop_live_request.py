"""The owner stop tool signals only the app's own headless Claude children."""
import unittest
from tools import stop_live_request as S

PS = '''  100     1 /venv/bin/python -B serve_current_warehouse.py --port 8908 --allow-live-stack
  101   100 /Users/x/.local/share/claude/versions/2.1.310 -p --model claude-sonnet-5-5 --system-prompt-file /tmp/a.txt --output-format json --tools
  102   100 /Users/x/.local/share/claude/versions/2.1.310 -p --model claude-opus-5-5 --system-prompt-file /tmp/b.txt --output-format stream-json
  103   100 /venv/bin/python -B helper.py -p --system-prompt-file /tmp/c.txt
  104     1 /Users/x/.local/share/claude/versions/2.1.310 -p --model claude-sonnet-5-5 --system-prompt-file /tmp/d.txt
  105   100 /Users/x/.local/share/claude/versions/2.1.310 --resume interactive-session
  200     1 /usr/bin/python3 -m http.server 8765
not a process line
'''


class Stop(unittest.TestCase):
    def test_only_direct_headless_claude_children_of_the_app_are_selected(self):
        rows = S.processes(PS)
        self.assertEqual(len(rows), 7)
        self.assertEqual([c['pid'] for c in S.claude_children(rows, 100, '/Users/x/.local/share/claude/versions/2.1.310')], [101, 102])
        self.assertEqual(S.claude_children(rows, 100, '/other/claude'), [])

    def test_a_listener_that_is_not_the_app_is_refused(self):
        rows = S.processes(PS)
        for pid in (200, 999):
            with self.assertRaisesRegex(ValueError, 'not the local Agere app'): S.claude_children(rows, pid, 'claude')


if __name__ == '__main__': unittest.main()
