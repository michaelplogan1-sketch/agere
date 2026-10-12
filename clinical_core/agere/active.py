"""Resolve the explicit active warehouse; never silently fall back after activation."""
import json
from pathlib import Path


def stack_path():
    root=Path(__file__).resolve().parents[2]
    config=root/'config/agere/active_warehouse.json'
    if not config.exists():
        return root/'artifacts/agere-population-stack-v3'
    value=json.loads(config.read_text());build=Path(value['build'])
    receipt=json.loads((build/'connection.json').read_text())
    if receipt['status']!='connected' or receipt['snapshot']!=value['snapshot']:
        raise ValueError('Active warehouse connection is stale; explicit repair required')
    return build/'stack'
