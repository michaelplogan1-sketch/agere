#!/usr/bin/env python3
"""Regenerate macros/input_layer_contract.sql from the installed Tuva Core Input Layer YAML.

Run from the dbt project directory after `dbt deps`:
    python3 scripts/generate_input_layer_contract.py
"""
import glob
import pathlib

import yaml

TYPE_MAP = {"varchar": "varchar", "date": "date", "integer": "integer", "timestamp": "timestamp",
            "float": "double", "number": "double", None: "varchar"}

root = pathlib.Path(__file__).resolve().parents[1]
items = []
for f in sorted(glob.glob(str(root / "dbt_packages/the_tuva_project/models/input_layer/*.yml"))):
    for m in yaml.safe_load(open(f))["models"]:
        cols = [(c["name"], TYPE_MAP[c.get("config", {}).get("meta", {}).get("data_type")]) for c in m["columns"]]
        items.append("  '%s': [%s]" % (m["name"].replace("input_layer__", ""),
                                        ", ".join("('%s','%s')" % c for c in cols)))
out = ["{#- Generated from dbt_packages/the_tuva_project/models/input_layer/*.yml (Tuva Core v1.0.0).",
       "    Column order and data types of every Tuva Input Layer table. Regenerate with",
       "    scripts/generate_input_layer_contract.py if the Tuva package is upgraded. -#}",
       "{% macro input_layer_contract() %}", "{%- set contract = {", ",\n".join(items),
       "} -%}", "{{ return(contract) }}", "{% endmacro %}"]
(root / "macros/input_layer_contract.sql").write_text("\n".join(out) + "\n")
print("wrote", len(items), "tables")
