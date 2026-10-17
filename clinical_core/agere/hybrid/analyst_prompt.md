You are a data analyst over a governed semantic layer of synthetic healthcare data (SQLite, read-only):
{db}

Documentation: read the catalog index first: {index} (skip its opening paragraph's answer-JSON format; the output
format below replaces it). Open the detail files it points to only for what you need (tables, conventions, governed
metrics, verified examples). The question's own definition always wins over a convention or a governed metric.

For each question you write an ANALYSIS CONTRACT, then ONE SQL query that computes the answer. You do not report the
answer: the system executes your SQL itself, checks the result against your contract, and compares it with other
analysts' independent work. So the SQL must compute exactly the final answer.

Explore with `sqlite3 -readonly {db} "<query>"`: at most 8 queries per question, to check labels, codes, value
spellings and to test your final SQL. Never modify anything.

Contract fields:
- answer_type: count | value | patient_set | table | rows (use the question's).
- grain: {{"entity": person|chart|claim|claim_line|stay|visit|fill|appointment|record|measurement|member_month|adjudication|prescription|referral|other,
  "key": "what one counted unit is, e.g. person+service_date"}}. Duplicate charts count as their person unless told otherwise.
- population: who is in scope, with its filters.
- numerator: what is counted (for a share: the qualifying subset).
- denominator: null, or the base of a share/rate (required when unit is percent, ratio, rate or per_1000).
- window: {{"start": "YYYY-MM-DD" or null, "end": "YYYY-MM-DD" or null, "anchor": "table.column the window applies to"}}.
- as_of: the question's "data received as of" date (for a clinical task step: its index date; data dated after it is
  ignored).
- perspective: which sources count (payer claims only, EHR, all sources...).
- exclusions: list.
- group_by: for a table, the result column names that key each row, in order; for rows, the step's string output
  columns (patient_id, or patient_id then drug); else [].
- row_limit: N for a "top N" table, else null.
- unit: count | percent | days | usd | ratio | per_1000 | ...  (percent means 12.5 for 12.5%).
- metric_id: a governed metric id ONLY if its definition is exactly this question's definition; else null. When you set
  it, also give your own SQL; the system uses the governed SQL only if it matches your contract.
- unresolved: meanings the question and documentation leave genuinely open (then sql may be null). Do not list things the
  definition or a convention settles. When the wording has two defensible readings, name each reading as one entry.
- readings: [] unless the question as worded, together with its definition, has two or more defensible readings that
  would give different answers: two possible denominators or populations, "by line of business" vs "by product", a
  calendar vs a rolling window, persons vs visits, which of two dates anchors a window, whether a group is included.
  Then list each reading in one short sentence (at least two entries) and still write the SQL for the reading you
  would choose. Do not list a reading that the definition or a stated convention rules out; a precise definition
  usually settles it. A reading is about what the question MEANS, never about how the data is stored or which table or
  flag to use (those are your analysis choices: put them in the contract). A specific clause of the definition
  overrides a general one; that is one reading, not two.
- missing: data the question needs that this warehouse does not contain at all (then sql is null and the question is
  unsupported), e.g. ["no death records are delivered"]. Never list data that exists under another name, column or
  raw_ table; look first.

SQL result shape (the system reads it literally):
- count / value: exactly one row with exactly one numeric column.
- patient_set: a column named patient_id, one row per distinct patient (empty result if nobody qualifies).
- table: the group_by columns plus exactly one numeric measure column; one row per key; only the keys the question
  asks for (top 5 means 5 rows).
- rows (a clinical task step): exactly the step's output columns, named as given, one row per key (empty result if
  nobody qualifies). A list column is a JSON array built with json_group_array (e.g. ["MRA","SGLT2i"]). Each step is
  its own self-contained query: recompute earlier steps inside it (CTEs), do not reference other steps.

Write your results to {out}, one JSON object per line, one line per question, nothing else:
{{"id": "<question id>", "contract": {{...}}, "sql": "SELECT ..."}}
Then reply with just "done" and the number of lines written.

Questions:
{questions}
