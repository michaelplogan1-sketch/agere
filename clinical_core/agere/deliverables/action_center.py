"""Trusted action workspace over saved /ask evidence and durable review work."""
from pathlib import Path


def page():
    assets = Path(__file__).parent
    return (assets / 'action_center.html').read_text().replace(
        '/* ACTION_CENTER_CSS */', (assets / 'action_center.css').read_text() + '\n' +
        (assets / 'workspace_skin.css').read_text()
    ).replace('/* ACTION_CENTER_JS */', (assets / 'action_center.js').read_text())
