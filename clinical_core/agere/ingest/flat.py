"""Delimited flat-file loader (EHR extracts, payer extracts, CMS CCLF/ALR CSV, programs, rosters, SDOH vendor).

Landing table = ``f"{feed}_{vendor_or_none}_{kind}"`` in snake_case, where ``kind`` is the file's table name with
dates, sequence numbers, instance ids and RESEND/v2/restatement markers stripped, so every site/month of the same kind
lands in the same table. Examples (relative to raw/):

    ehr/athena/2881/2027-11/PATIENT.csv                         -> ehr_athena_patient
    ehr/epic/EPIC-TX/2027-04/hno_note_text.txt                  -> ehr_epic_hno_note_text
    payers/WH-PAYER-MCD2/2026-09/MCD2_MEDICAL_20261005.csv      -> payers_medical        (payer='MCD2')
    cms_cclf_csv/partd_claims/P.A9999.ACO.ZC7Y26.D261012.T..csv -> cclf_partd_claims
    cms_alr/2030/P.A9999.ACO.AALR.D300115.T8118695_1-1.csv      -> alr_aalr1_assigned_beneficiaries
    programs/WH-PAYER-X1/2030Q1/X1_CARE_GAPS_2030Q1.csv         -> programs_care_gaps    (payer='X1')
    programs/mssp/2030Q1/P.A9999.ACO.QALR.2030Q1.csv            -> programs_mssp_qalr
    rosters/2026-09/ACO_PROVIDER_ROSTER_20260910.csv            -> rosters_aco_provider_roster
    sdoh_vendor/2027-01/SDOH_VENDOR_20270115.csv                -> sdoh_vendor

Every value is landed as the TEXT the sender wrote (no coercion, no stripping). Column names are snake_cased (payer-style
feeds also split camelCase / spaces so C3's ``claimId`` and C2's ``Claim Id`` meet ``claim_id``); EHR/CMS vendor column
names are only lowercased. Header differences between files of one kind never fail a load: rows carry their own columns
(the downstream union is the superset) and differences are recorded in ``flat_schema_drift``.

Ragged rows (truncated deliveries) are kept: missing fields stay absent and ``flat_row_issue`` says so; surplus
fields go to ``flat_extra`` (JSON list). Non-delimited ``.txt`` files land as one row each in ``<feed>_note_text``
(``ehr_<vendor>_note_text`` for EHR feeds). The fixed-width ``cms_cclf`` folder is skipped (``cms_cclf_csv`` carries
the same data).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

SCHEMA_DRIFT_TABLE = "flat_schema_drift"

FEEDS = {"ehr", "payers", "cms_cclf_csv", "cms_alr", "programs", "rosters", "sdoh_vendor"}
PAYER_STYLE = {"payers", "programs", "rosters", "sdoh_vendor"}  # camelCase/space header normalization
EXTS = {".csv", ".txt", ".tsv", ".psv", ".dat"}

ALR_TABLES = {
    "1": "aalr1_assigned_beneficiaries",
    "2": "aalr2_assigned_beneficiaries_tin",
    "4": "aalr4_assigned_beneficiaries_tin_npi",
    "5": "aalr5_beneficiary_turnover",
    "6": "aalr6_beneficiaries_assignable_or_voluntary",
    "9": "aalr9_beneficiaries_underserved",
}

csv.field_size_limit(1 << 28)

# ---------------------------------------------------------------------------------------------------------------------
# path interpretation
# ---------------------------------------------------------------------------------------------------------------------


def _locate(path: Path, context: dict | None = None) -> tuple[str, tuple[str, ...]] | None:
    """(feed, parts after the feed folder incl. filename) or None when the file is not ours."""
    if context and context.get("relpath"):
        rel = PurePosixPath(str(context["relpath"]).replace("\\", "/")).parts
        if rel and "private" not in rel:
            return rel[0], tuple(rel[1:])
    parts = Path(path).parts
    if "private" in parts:
        return None
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] in FEEDS or parts[i] in ("cms_cclf", "fhir_bulk"):
            return parts[i], tuple(parts[i + 1:])
    return None


def detect(path: Path) -> bool:
    p = Path(path)
    if p.suffix.lower() not in EXTS:
        return False
    loc = _locate(p)
    if loc is None:
        return False
    feed, rest = loc
    if feed not in FEEDS:  # includes cms_cclf (fixed width) and fhir_bulk
        return False
    if feed == "ehr" and (not rest or rest[0].lower() == "fhir"):
        return False
    return True


def snake(name: str, split_camel: bool = False) -> str:
    s = str(name).strip()
    if split_camel:
        s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
        s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


_NOISE_TOKEN = re.compile(r"^(\d{4,}|d\d{6}|t\d+|\d{4}q[1-4]|py\d{4})$", re.I)
_CMS_PREFIX = re.compile(r"^P\.A\d{4}\.ACO\.", re.I)
_VARIANT_TOKENS = {"resend": "resend", "restatement": "full_restatement"}


def _kind_tokens(stem: str) -> tuple[list[str], str | None]:
    """Split a file stem on . and _ ; drop dates/sequence numbers/CMS prefixes; return (kind tokens, file_variant)."""
    toks = [t for t in re.split(r"[._\s]+", _CMS_PREFIX.sub("", stem)) if t]
    has_restatement = any(t.lower() == "restatement" for t in toks)
    variant = None
    kept: list[str] = []
    for t in toks:
        low = t.lower()
        if low in _VARIANT_TOKENS:
            variant = _VARIANT_TOKENS[low]
        elif low == "full" and has_restatement:
            continue
        elif re.fullmatch(r"v\d+", low):
            variant = low
        elif _NOISE_TOKEN.match(t):
            continue
        else:
            kept.append(low)
    return kept, variant


def classify(path: Path, context: dict | None = None) -> dict | None:
    """Work out landing table + metadata columns for a file. None if not a flat-feed file."""
    loc = _locate(path, context)
    if loc is None:
        return None
    feed, rest = loc
    if feed not in FEEDS or not rest:
        return None
    p = Path(path)
    stem = p.name[: -len(p.suffix)] if p.suffix else p.name
    meta: dict[str, Any] = {}
    note_table = f"{feed}_note_text"
    if feed == "ehr":
        if len(rest) < 2:
            return None
        vendor = snake(rest[0])
        if vendor == "fhir":
            return None
        toks, variant = _kind_tokens(stem)
        kind = snake("_".join(toks))
        table = f"ehr_{vendor}_{kind}"
        note_table = f"ehr_{vendor}_note_text"
    elif feed == "cms_cclf_csv":
        if len(rest) < 2:
            return None
        table = f"cclf_{snake(rest[0])}"
        _, variant = _kind_tokens(stem)
    elif feed == "cms_alr":
        m = re.search(r"_1-(\d+)(.*)$", stem)
        toks, variant = _kind_tokens(stem[: m.start()] if m else stem)
        alr_type = next((t.upper() for t in toks if t in ("aalr", "qalr")), None)
        if m:
            if "resend" in m.group(2).lower():
                variant = "resend"
            kind = ALR_TABLES.get(m.group(1), f"aalr{m.group(1)}")
        else:
            kind = snake("_".join(t for t in toks if t not in ("aalr", "qalr")))
        table = f"alr_{kind}"
        note_table = "alr_note_text"
        meta["alr_type"] = alr_type
    elif feed == "payers":
        payer = _payer_id(rest)
        toks, variant = _kind_tokens(stem)
        if toks and payer and toks[0] == payer.lower():
            toks = toks[1:]
        table = f"payers_{snake('_'.join(toks))}"
        meta["payer"] = payer
    elif feed == "programs":
        toks, variant = _kind_tokens(stem)
        sub = rest[0] if len(rest) > 1 else None
        payer = _payer_id(rest)
        if payer:
            if toks and toks[0] == payer.lower():
                toks = toks[1:]
            meta["payer"] = payer
            table = f"programs_{snake('_'.join(toks))}"
        else:
            table = "programs_" + "_".join(x for x in (snake(sub) if sub else "", snake("_".join(toks))) if x)
    else:  # rosters, sdoh_vendor
        toks, variant = _kind_tokens(stem)
        kind = snake("_".join(toks))
        table = kind if kind == feed or kind.startswith(feed + "_") else f"{feed}_{kind}"
    table = re.sub(r"_+", "_", table).strip("_")
    return {
        "feed": feed,
        "rest": rest,
        "table": table,
        "note_table": note_table,
        "variant": variant,
        "meta": meta,
        "split_camel": feed in PAYER_STYLE,
    }


def _payer_id(rest: tuple[str, ...]) -> str | None:
    if len(rest) < 2:
        return None
    folder = rest[0]
    if folder.upper().startswith("WH-PAYER-"):
        return folder[len("WH-PAYER-"):]
    return None


# ---------------------------------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _rows(text: str, delim: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delim, quotechar='"'))


_HEADER_FIELD = re.compile(r"^[\w][\w .\-/()#%]*$")


def _looks_like_header(fields: list[str]) -> bool:
    """Column names, not prose: short, no leading/trailing blanks, no sentence punctuation."""
    return len(fields) >= 2 and all(
        f and f == f.strip() and len(f) <= 64 and _HEADER_FIELD.match(f) for f in fields
    )


def sniff_delimiter(text: str) -> str | None:
    """',' '|' or tab when the text is a delimited table; None for free text.

    Delimited when >= 80% of the first rows have the header's field count, or when the header line itself looks like
    a list of column names (truncated deliveries have ragged rows, so row consistency alone is not enough)."""
    sample_lines = []
    for line in text.splitlines():
        if line.strip():
            sample_lines.append(line)
        if len(sample_lines) >= 30:
            break
    if not sample_lines:
        return None
    sample = "\n".join(sample_lines)
    best, best_score = None, 0.0
    for d in ("\t", "|", ","):
        if d not in sample_lines[0]:
            continue
        try:
            rows = [r for r in _rows(sample, d) if r]
        except csv.Error:
            continue
        width = len(rows[0])
        if width < 2:
            continue
        score = sum(1 for r in rows if len(r) == width) / len(rows)
        if score < 0.8 and _looks_like_header(rows[0]):
            score = 0.8
        score = score + 0.01 * width  # tie-break toward the delimiter that splits the header the most
        if score > best_score:
            best, best_score = d, score
    if best is None or best_score < 0.8:
        return None
    return best


def _clean_header(raw_header: list[str], split_camel: bool) -> list[str]:
    cols: list[str] = []
    seen: dict[str, int] = {}
    for i, h in enumerate(raw_header):
        c = snake(h, split_camel)
        if not c:
            c = f"col_{i + 1}"
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 1
        cols.append(c)
    return cols


# header registry: first-seen normalized header per landing table, for drift detection (process lifetime)
_BASELINE: dict[str, tuple[list[str], str]] = {}
_RAW_VARIANTS: dict[str, set[str]] = {}


def reset_schema_registry() -> None:
    _BASELINE.clear()
    _RAW_VARIANTS.clear()


def _drift_rows(table: str, cols: list[str], raw_header: list[str], rel: str) -> Iterator[tuple[str, dict]]:
    sig = hashlib.sha1(json.dumps(cols).encode()).hexdigest()[:12]
    if table not in _BASELINE:
        _BASELINE[table] = (cols, rel)
        _RAW_VARIANTS[table] = {json.dumps(raw_header)}
        return
    base_cols, base_file = _BASELINE[table]
    if cols != base_cols:
        added = [c for c in cols if c not in base_cols]
        removed = [c for c in base_cols if c not in cols]
        issue = (
            "columns_added_removed" if added and removed else "columns_added" if added
            else "columns_removed" if removed else "column_order"
        )
        yield SCHEMA_DRIFT_TABLE, {
            "landing_table": table,
            "relpath": rel,
            "issue": issue,
            "added_columns": json.dumps(added) if added else None,
            "removed_columns": json.dumps(removed) if removed else None,
            "header_signature": sig,
            "n_columns": len(cols),
            "baseline_n_columns": len(base_cols),
            "baseline_file": base_file,
            "raw_header": json.dumps(raw_header),
        }
    else:
        key = json.dumps(raw_header)
        if key not in _RAW_VARIANTS[table]:  # same normalized columns, different spelling: record once per variant
            _RAW_VARIANTS[table].add(key)
            yield SCHEMA_DRIFT_TABLE, {
                "landing_table": table,
                "relpath": rel,
                "issue": "raw_header_spelling",
                "added_columns": None,
                "removed_columns": None,
                "header_signature": sig,
                "n_columns": len(cols),
                "baseline_n_columns": len(base_cols),
                "baseline_file": base_file,
                "raw_header": key,
            }


def _text_row(path: Path, text: str, info: dict) -> dict:
    p = Path(path)
    stem = p.stem
    m = re.search(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)", stem)
    file_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    ids = [x for x in re.findall(r"\d{4,}", stem) if not (m and x in (m.group(0).replace("-", "").replace("_", "")))]
    rest = info["rest"]
    row = {
        "file_stem": stem,
        "note_id": ids[0] if ids else None,
        "file_ids": json.dumps(ids) if ids else None,
        "file_date": file_date,
        "instance": rest[1] if info["feed"] == "ehr" and len(rest) > 2 else (rest[0] if len(rest) > 1 else None),
        "drop": next((x for x in rest[:-1] if re.fullmatch(r"\d{4}-\d{2}|\d{4}Q[1-4]|\d{4}", x)), None),
        "file_variant": info["variant"],
        "line_count": len(text.splitlines()),
        "char_count": len(text),
        "text": text,
    }
    return row


def parse(path: Path, context: dict | None = None) -> Iterator[tuple[str, dict]]:
    info = classify(path, context)
    if info is None:
        return
    raw = Path(path).read_bytes()
    if not raw.strip():
        return
    text = _decode(raw)
    delim = sniff_delimiter(text)
    if delim is None:
        yield info["note_table"], _text_row(path, text, info)
        return
    table = info["table"]
    rel = "/".join((info["feed"],) + tuple(info["rest"]))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim, quotechar='"')
    header: list[str] | None = None
    for rec in reader:
        if header is None:
            if not rec or all(not x.strip() for x in rec):
                continue
            header = rec
            cols = _clean_header(header, info["split_camel"])
            yield from _drift_rows(table, cols, header, rel)
            meta = {}
            for k, v in info["meta"].items():
                meta[(k if k not in cols else "flat_" + k)] = v
            vkey = "file_variant" if "file_variant" not in cols else "flat_file_variant"
            meta[vkey] = info["variant"]
            width = len(cols)
            continue
        if not rec or (len(rec) == 1 and not rec[0].strip()):
            continue  # blank / whitespace-padding line
        row: dict[str, Any] = dict(zip(cols, rec))
        n = len(rec)
        if n < width:
            row["flat_row_issue"] = f"short_row:{n}/{width}"
        elif n > width:
            row["flat_row_issue"] = f"long_row:{n}/{width}"
            row["flat_extra"] = json.dumps(rec[width:], ensure_ascii=False)
        row.update(meta)
        yield table, row
