"""Join executed result objects in code, preserving grain and intact source rows.

This is a read-only investigator tool, not a clinical plan or a disease handler.
A scalar join must prove uniqueness; a collection join preserves every matching
row, including its original quote/date/record relationships. Missingness remains
missingness. The base result, never the joins, defines output membership.
"""
import json
from ...contracts import digest

VERSION='result-assembly/1'


def measure(results,spec):
    """Compute a claim from complete native rows with explicit scope and domain.

    This uses the same native result objects as table assembly. It does not infer
    a domain from a label or a unit from a number. The declared predicate, grain,
    population and missing values remain part of the final claim's evidence.
    """
    from decimal import Decimal
    required={'result_view','operation','grain','unit','population','domain'}
    if not isinstance(spec,dict) or not required<=set(spec) or set(spec)-required-{'where','column','distinct','null_policy'}:
        raise ValueError('Measure needs native result_view, operation, grain, unit, population and domain')
    if not isinstance(spec['result_view'],str) or spec['result_view'] not in results:raise ValueError('Measure source must be an executed result_view')
    columns,rows=_rows(results[spec['result_view']])
    grain=spec['grain']
    if not isinstance(grain,list) or not grain or not all(isinstance(k,str) for k in grain) or len(set(grain))!=len(grain) or not set(grain)<=set(columns):
        raise ValueError('Measure grain must name distinct received source columns')
    if not isinstance(spec['unit'],str) or not spec['unit'].strip():raise ValueError('Measure unit must be explicit')
    def key(row,names):
        values=[row[k] for k in names]
        if any(v is None or isinstance(v,(dict,list)) for v in values):raise ValueError('Measure keys must be observed scalars')
        return json.dumps(values,sort_keys=True,allow_nan=False)
    identities=[key(r,grain) for r in rows]
    if len(set(identities))!=len(identities):raise ValueError('Measure source violates its declared row grain')
    population=spec['population']
    if not isinstance(population,dict) or set(population)!={'result_view','on'} or not isinstance(population['result_view'],str) or population['result_view'] not in results:
        raise ValueError('Measure population must bind to an executed result_view and exact key mapping')
    pop_columns,pop_rows=_rows(results[population['result_view']]);on=population['on']
    if not isinstance(on,dict) or not on or not set(on)<=set(columns) or not all(isinstance(v,str) for v in on.values()) or not set(on.values())<=set(pop_columns):
        raise ValueError('Measure population mapping uses unavailable keys')
    left=list(on);right=[on[k] for k in left];members=[key(r,right) for r in pop_rows]
    if len(set(members))!=len(members):raise ValueError('Measure population keys must be unique')
    members=set(members);scoped=[r for r in rows if key(r,left) in members]
    def scalar(v):return v is None or type(v) in (str,int,float,bool)
    def canonical(v):return json.dumps(v,sort_keys=True,allow_nan=False)
    def predicate(condition,*,domain=False):
        required={'column','values'} if domain else {'column','op','value'}
        if not isinstance(condition,dict) or set(condition)!=required or not isinstance(condition['column'],str) or condition['column'] not in columns:
            raise ValueError('Measure predicate must bind to a received source column')
        op='in' if domain else condition['op'];value=condition['values'] if domain else condition['value']
        if op not in ('eq','ne','in','not_in'):raise ValueError('Use exact equality or membership predicates')
        if op in ('in','not_in'):
            if not isinstance(value,list) or not value or not all(scalar(v) for v in value):raise ValueError('Membership needs explicit scalar values')
            accepted={canonical(v) for v in value}
            return lambda row:(canonical(row[condition['column']]) in accepted)==(op=='in')
        if not scalar(value):raise ValueError('Equality needs a scalar value')
        return lambda row:(canonical(row[condition['column']])==canonical(value))==(op=='eq')
    domain=spec['domain']
    # null explicitly means all received domains; it must not be relabeled as a
    # narrower domain. Source-based semantic checking still reviews that label.
    domain_test=predicate(domain,domain=True) if domain is not None else lambda row:True
    where=spec.get('where',[])
    if not isinstance(where,list):raise ValueError('Measure where must be a list of source predicates')
    filters=[predicate(p) for p in where]
    domain_rows=[r for r in scoped if domain_test(r)]
    selected=[r for r in domain_rows if all(f(r) for f in filters)]
    operation=spec['operation'];missing=0
    if operation=='count_rows':
        if set(spec)&{'column','distinct','null_policy'}:raise ValueError('Row counts do not take numeric or distinct fields')
        value=len(selected)
    elif operation=='count_distinct':
        distinct=spec.get('distinct')
        if not isinstance(distinct,list) or not distinct or not all(isinstance(k,str) for k in distinct) or len(set(distinct))!=len(distinct) or not set(distinct)<=set(columns):
            raise ValueError('Distinct count needs exact source columns')
        if set(spec)&{'column','null_policy'}:raise ValueError('Distinct counts do not take numeric fields')
        values=[]
        for row in selected:
            if any(row[k] is None for k in distinct):missing+=1
            else:values.append(key(row,distinct))
        value=len(set(values)) if not missing else None
    elif operation=='sum':
        column=spec.get('column');policy=spec.get('null_policy','unknown')
        if not isinstance(column,str) or column not in columns or not isinstance(policy,str) or policy not in ('unknown','exclude') or 'distinct' in spec:raise ValueError('Sum needs a received numeric column and explicit null policy')
        values=[]
        for row in selected:
            v=row[column]
            if v is None:missing+=1;continue
            if type(v) not in (int,float) or not Decimal(str(v)).is_finite():raise ValueError('Sum source must contain finite numeric values')
            values.append(Decimal(str(v)))
        value=None if missing and policy=='unknown' else str(sum(values,Decimal(0)))
    else:raise ValueError('Use count_rows, count_distinct or sum')
    return {'value':value,'contract':spec,'source_rows':len(rows),'population_members':len(pop_rows),
        'rows_outside_population':len(rows)-len(scoped),'rows_in_domain':len(domain_rows),'selected_rows':len(selected),
        'missing_value_rows':missing,'source_result_digest':digest(results[spec['result_view']]),
        'population_result_digest':digest(results[population['result_view']]),
        'meaning':'Native computation under declared source scope/domain/grain/unit. Source-supported unknowns remain unknown; no clinical interpretation inferred.'}


def _rows(result):
    columns=result.get('columns');rows=result.get('rows')
    if not isinstance(rows,list):rows=next((result[k]for k in ('table','documents','passages')if isinstance(result.get(k),list)),None)
    if not isinstance(rows,list):raise ValueError('An executed tabular result is required')
    if result.get('truncated'):raise ValueError('Complete result required; query with complete=true first')
    if type(result.get('table_rows')) is int and result['table_rows']!=len(rows):raise ValueError('Native result rows do not match their declared complete cardinality')
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
        if not isinstance(join,dict)or not {'result','as','on','mode'}<=set(join)or set(join)-{'result','as','on','mode','columns','items_from','retain_in_final'}:raise ValueError('Join requires result, as, on and mode, with optional columns or items_from')
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
        items_from=join.get('items_from');collections={}
        retain=join.get('retain_in_final',False)
        if type(retain)is not bool or retain and items_from is None:raise ValueError('Final retention requires an explicit native collection')
        if items_from is not None:
            if mode!='collect' or not isinstance(items_from,str) or items_from not in right_columns or 'columns' in join:
                raise ValueError('items_from requires collect and a received collection column, without a columns projection')
            for index,r in enumerate(right):
                value=r[items_from]
                if isinstance(value,str):
                    try:value=json.loads(value)
                    except ValueError as exc:raise ValueError('items_from must contain a native JSON collection') from exc
                if value is None:value=[]
                if not isinstance(value,list):raise ValueError('items_from must contain an array or null')
                collections[id(r)]=value
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
            row[alias]=([item for v in found for item in collections[id(v)]] if found else None) if items_from is not None else (
                [{c:v[c]for c in selected}for v in found]if found and mode=='collect'else {c:found[0][c]for c in selected}if found else None)
            if found:matched+=1;matched_right.add(k)
        columns.append(alias)
        coverage.append({'result_view':view,'as':alias,'mode':mode,'on':on,'selected_columns':selected,'source_rows':len(right),
            'base_rows_matched':matched,'base_rows_without_match':len(rows)-matched,
            'source_rows_with_missing_key':missing,'source_rows_outside_base_keys':sum(len(v)for k,v in grouped.items()if k not in matched_right),
            'source_result_digest':digest(results[view]),
            **({'items_from':items_from,'source_collection_items':sum(map(len,collections.values())),
                'output_collection_items':sum(len(row[alias] or []) for row in keyed.values()),'retain_in_final':retain} if items_from is not None else {})})
    return {'columns':columns,'rows':list(keyed.values()),'truncated':False,
        'assembly':{'schema':VERSION,'base_result_view':base,'base_result_digest':digest(results[base]),'keys':keys,
            'base_rows':len(rows),'output_rows':len(keyed),'joins':coverage,
            'method':'Exact left membership; scalar joins prove uniqueness; collection joins retain intact rows. Missing joins are null, not a clinical negative.'}}


def collection_retention_issues(results,final_rows,dependencies):
    """Check explicitly promised source collections in descendant final tables."""
    from collections import Counter
    issues=[]
    if dependencies is None:return issues
    def decoded(value):
        if isinstance(value,str) and value[:1] in ('{','['):
            try:return json.loads(value)
            except ValueError:return value
        return value
    def key(row,names):
        if not isinstance(row,dict) or any(k not in row for k in names):return None
        return json.dumps([row[k]for k in names],sort_keys=True,allow_nan=False)
    for view in sorted(dependencies):
        result=results[view];assembly=result.get('assembly') or {};keys=assembly.get('keys') or []
        required=[j for j in assembly.get('joins',[]) if j.get('retain_in_final')]
        if not required:continue
        _,source=_rows(result);final={key(row,keys):row for row in final_rows if key(row,keys) is not None}
        for row in source:
            ident=key(row,keys)
            if ident not in final:continue
            target=final[ident]
            for join in required:
                alias=join['as'];expected=decoded(row[alias]) or []
                value=decoded(target.get(alias))
                if alias not in target or value is not None and not isinstance(value,list):
                    issues.append('Final field '+alias+' at '+str(ident)+' omits its explicitly retained native collection');continue
                wanted=Counter(digest(item)for item in expected);observed=Counter(digest(item)for item in value or [])
                missing=wanted-observed
                if missing:
                    records=sorted({item['record_id']for item in expected if isinstance(item,dict) and isinstance(item.get('record_id'),str) and digest(item)in missing})
                    issues.append('Final field '+alias+' at '+str(ident)+' drops or changes '+str(sum(missing.values()))+
                        ' retained source objects from '+view+'; record_ids='+json.dumps(records))
    return list(dict.fromkeys(issues))
