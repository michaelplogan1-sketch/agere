{#- Select every Tuva Input Layer column for `table_name`, in contract order and
    with contract types, from relation `rel`. A contract column the mapping
    does not produce is emitted as a typed null. Every column of `rel` that
    starts with the passthrough prefix (x_) is appended as an extension column. -#}
{% macro tuva_input_select(table_name, rel) %}
  {%- set contract = input_layer_contract()[table_name] -%}
  {%- set prefix = var('passthrough', {}).get('prefix', 'x_') -%}
  {%- set have = [] -%}
  {%- if execute -%}
    {%- for c in adapter.get_columns_in_relation(rel) -%}
      {%- do have.append(c.name | lower) -%}
    {%- endfor -%}
  {%- endif -%}
  {%- set cols = [] -%}
  {%- for col, typ in contract -%}
    {%- set qcol = adapter.quote(col) if col == 'plan' else col -%}
    {%- if col in have -%}
      {%- do cols.append('cast(' ~ qcol ~ ' as ' ~ typ ~ ') as ' ~ qcol) -%}
    {%- else -%}
      {%- do cols.append('cast(null as ' ~ typ ~ ') as ' ~ qcol) -%}
    {%- endif -%}
  {%- endfor -%}
  {%- for c in have -%}
    {%- if c.startswith(prefix) -%}{%- do cols.append(c) -%}{%- endif -%}
  {%- endfor %}
select
    {{ cols | join('\n  , ') }}
from {{ rel }}
{% endmacro %}

{#- JSON field accessors over the `j` payload column (DuckDB). Parenthesised so
    they compose safely with comparison operators. -#}
{% macro js(field, alias=none, j='j') -%}
  nullif(({{ j }} ->> '{{ field }}'), ''){% if alias %} as {{ alias }}{% endif %}
{%- endmacro %}

{% macro jnum(field, alias=none, j='j') -%}
  try_cast(({{ j }} ->> '{{ field }}') as double){% if alias %} as {{ alias }}{% endif %}
{%- endmacro %}

{% macro jint(field, alias=none, j='j') -%}
  try_cast(({{ j }} ->> '{{ field }}') as bigint){% if alias %} as {{ alias }}{% endif %}
{%- endmacro %}

{% macro jdate(field, alias=none, j='j') -%}
  try_cast(left(({{ j }} ->> '{{ field }}'), 10) as date){% if alias %} as {{ alias }}{% endif %}
{%- endmacro %}

{% macro jts(field, alias=none, j='j') -%}
  try_cast(({{ j }} ->> '{{ field }}') as timestamptz)::timestamp{% if alias %} as {{ alias }}{% endif %}
{%- endmacro %}

{#- Rows of one raw stream from the staged record table. -#}
{% macro stream(name) -%}
  (select * from {{ ref('stg_raw__records') }} where stream = '{{ name }}')
{%- endmacro %}

{#- ICD-10 normalisation for the input layer: Tuva expects codes without dots. -#}
{% macro icd10_clean(expr) -%}
  nullif(upper(replace(trim({{ expr }}), '.', '')), '')
{%- endmacro %}
