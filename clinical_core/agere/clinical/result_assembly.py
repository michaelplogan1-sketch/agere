"""Join executed result objects in code, preserving grain and intact source rows.

This is a read-only investigator tool, not a clinical plan or a disease handler.
A scalar join must prove uniqueness; a collection join preserves every matching
row, including its original quote/date/record relationships. Missingness remains
missingness. The base result, never the joins, defines output membership.
"""
import json
from ...contracts import digest

VERSION='result-assembly/1'


def _rows(result):
    columns=result.get('columns');rows=result.get('rows')
    if not isinstance(rows,list):rows=next((result[k]for k in ('table','documents','passages')if isinstance(result.get(k),list)),None)
    if not isinstance(rows,list):raise ValueError('An executed tabular result is required')
    if result.get('truncated'):raise ValueError('Complete result required; query with complete=true first')
    if columns is None:columns=list(dict.fromkeys(k for r in rows if isinstance(r,dict)for k in r))
    if not isinstance(columns,list)or not all(isinstance(c,str)for c in columns)or len(columns)!=len(set(columns)):
        raise ValueError('Distinct received column names required')
    columns=list(columns)
    output=[]
    for row in rows:
        if isinstance(row,dict):
            if set(row)-set(columns):raise ValueError('Row contains an undeclared column')
            output.append({c:row.get(c)for c in columns})
        elif isinstance(row,list)and len(row)==len(columns):output.append(dict(zip(columns,row)))
        else:raise ValueError('Invalid received result row')
    return columns,output


def compose(results,*,base,keys,joins=None,base_columns=None):
    if base not in results:raise ValueError('base must name an executed result_view')
    if not isinstance(keys,list)or not keys or len(keys)!=len(set(keys))or not all(isinstance(k,str)for k in keys):
        raise ValueError('Nonempty distinct base keys required')
    columns,rows=_rows(results[base])
    if base_columns is not None:
        if not isinstance(base_columns,list)or not all(isinstance(c,str)for c in base_columns)or len(base_columns)!=len(set(base_columns))or not set(keys)<=set(base_columns)or not set(base_columns)<=set(columns):
            raise ValueError('base_columns must be distinct received columns including all keys')
        columns=list(base_columns);rows=[{c:r[c]for c in columns}for r in rows]
    if not set(keys)<=set(columns):raise ValueError('Base key not in received columns')
    def identity(row,names):
        vals=[row[k]for k in names]
        if any(v is None for v in vals):return None
        if any(isinstance(v,(dict,list))for v in vals):raise ValueError('Join keys must be scalar')
        # JSON identity preserves strings versus numbers and exact source grain.
        return json.dumps(vals,sort_keys=True,allow_nan=False)
    keyed={}
    for row in rows:
        k=identity(row,keys)
        if k is None:raise ValueError('Base result contains a missing key; establish its grain first')
        if k in keyed:raise ValueError('Base keys are not unique; establish the intended grain first')
        keyed[k]=dict(row)
    coverage=[]
    if joins is None:joins=[]
    if not isinstance(joins,list):raise ValueError('joins must be a list')
    for join in joins:
        if not isinstance(join,dict)or not {'result','as','on','mode'}<=set(join)or set(join)-{'result','as','on','mode','columns'}:raise ValueError('Join requires result, as, on and mode only')
        view,alias,on,mode=(join[x]for x in ('result','as','on','mode'))
        if view not in results:raise ValueError('Join must name an executed result_view')
        if not isinstance(alias,str)or not alias or alias in columns:raise ValueError('Join alias must be a new nonempty field')
        if mode not in ('one','collect'):raise ValueError('Use one for proven unique rows, collect for every matching source row')
        if not isinstance(on,dict)or not on or not set(on)<=set(keys)or not all(isinstance(v,str)for v in on.values()):
            raise ValueError('on maps received base key columns to received join columns')
        right_columns,right=_rows(results[view]);left_names=list(on);right_names=[on[k]for k in left_names]
        if not set(right_names)<=set(right_columns):raise ValueError('Join key not in received columns')
        selected=join.get('columns',right_columns)
        if not isinstance(selected,list)or not selected or not all(isinstance(c,str)for c in selected)or len(selected)!=len(set(selected))or not set(selected)<=set(right_columns):raise ValueError('Join columns must be distinct received names')
        grouped={};missing=0
        for r in right:
            k=identity(r,right_names)
            if k is None:missing+=1;continue
            grouped.setdefault(k,[]).append(r)
        eligible={identity(r,left_names)for r in keyed.values()}
        if mode=='one'and any(len(v)!=1 for k,v in grouped.items()if k in eligible):raise ValueError('Scalar join is not unique; use collect to retain all rows')
        matched=0;matched_right=set()
        for row in keyed.values():
            k=identity(row,left_names);found=grouped.get(k)
            row[alias]=[{c:v[c]for c in selected}for v in found]if found and mode=='collect'else {c:found[0][c]for c in selected}if found else None
            if found:matched+=1;matched_right.add(k)
        columns.append(alias)
        coverage.append({'result_view':view,'as':alias,'mode':mode,'on':on,'selected_columns':selected,'source_rows':len(right),
            'base_rows_matched':matched,'base_rows_without_match':len(rows)-matched,
            'source_rows_with_missing_key':missing,'source_rows_outside_base_keys':sum(len(v)for k,v in grouped.items()if k not in matched_right),
            'source_result_digest':digest(results[view])})
    return {'columns':columns,'rows':list(keyed.values()),'truncated':False,
        'assembly':{'schema':VERSION,'base_result_view':base,'base_result_digest':digest(results[base]),'keys':keys,
            'base_rows':len(rows),'output_rows':len(keyed),'joins':coverage,
            'method':'Exact left membership; scalar joins prove uniqueness; collection joins retain intact rows. Missing joins are null, not a clinical negative.'}}
