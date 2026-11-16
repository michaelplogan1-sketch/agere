"""The router: code plus Tier 2. A question starts at Tier 0 and climbs one rung only when the current rung
does not apply, is not confident, or its output fails deterministic validation. Every escalation is recorded
with its reason. The frontier model is the last rung; its answer is returned even when validation fails,
labelled partial with the failed checks."""
import time

from . import bottles as bottle_store, governed, models, read as reader, recipes, store

ALL_TIERS = ('bottles', 'recipes', 'system1', 'slm', 'near', 'llm')
TOKEN_CEILING = 60000


def answer(question, **kw):
    """-> {'status','final_tier','columns','rows','recipe','validation','steps','seconds','escalations'}.

    A question with two plain readings (readings.py) is answered both ways. When the readings agree the answer is given
    once; when they differ the status is 'needs_choice' with each reading's answer under 'readings', and nothing is guessed."""
    from . import readings
    two = readings.detect(question)
    if not two: return _ladder(question, **kw)
    started = time.monotonic()
    outs = [(r, _ladder(r['question'], **kw)) for r in two['readings']]
    steps = [{**s, 'reading': r['id']} for r, o in outs for s in o['steps']]
    escalations = [e for _, o in outs for e in o['escalations']]
    seconds = round(time.monotonic() - started, 2)
    answered = [(r, o) for r, o in outs if o['status'] == 'answered']
    if len(answered) == len(outs) and all(readings.same(answered[0][1]['rows'], o['rows'], question) for _, o in answered[1:]):
        r, o = answered[0]
        return {**o, 'steps': steps, 'escalations': escalations, 'seconds': seconds,
                'readings_checked': {'rule': two['rule'], 'agree': True, 'readings': [x['label'] for x in two['readings']]}}
    if len(answered) == 1:   # one reading could not be answered: the other is given, and says which reading it is
        r, o = answered[0]
        return {**o, 'steps': steps, 'escalations': escalations, 'seconds': seconds,
                'readings_checked': {'rule': two['rule'], 'agree': None, 'answered_as': r['label'], 'question': r['question']}}
    if not answered:
        o = outs[0][1]
        return {**o, 'steps': steps, 'escalations': escalations, 'seconds': seconds}
    return {'status': 'needs_choice', 'final_tier': 'readings', 'recipe': None, 'columns': None, 'rows': None, 'validation': None,
            'steps': steps, 'escalations': escalations, 'seconds': seconds, 'ask': two['ask'], 'rule': two['rule'],
            'readings': [{**r, 'status': o['status'], 'final_tier': o['final_tier'], 'columns': o['columns'], 'rows': o['rows'],
                          'recipe': o['recipe'], 'validation': o['validation']} for r, o in outs]}


def _ladder(question, *, tiers=ALL_TIERS, bottles=None, bottle_new=False, token_ceiling=TOKEN_CEILING, s1_ledger=None,
            bottle_statuses=('promoted',), readers=reader.READERS, cohort=None):
    """One question up the ladder."""
    started = time.monotonic()
    steps, escalations = [], []
    db = store.connect(cohort)        # recipes execute here: inside the cohort when one is given
    lookup = store.connect() if cohort is not None else db  # vocabularies and identifiers are looked up in the whole layer
    def spent(): return sum(s.get('tokens', 0) for s in steps)
    def done(status, tier, recipe=None, columns=None, rows=None, validation=None, **extra):
        db.close()
        if lookup is not db: lookup.close()
        if evidence: extra = {**extra, 'documents': evidence['documents']}
        return {'status': status, 'final_tier': tier, 'recipe': recipe, 'columns': columns, 'rows': rows, 'validation': validation,
                'steps': steps, 'escalations': escalations, 'seconds': round(time.monotonic() - started, 2), **extra}
    evidence = {}
    def attempt(tier, sql, params, grain=None, total=None, read=None):
        """Execute and validate one proposal. -> (columns, rows, validation) or None with the escalation recorded."""
        t0 = time.monotonic()
        try:
            if tier == 'llm':
                narrowed = recipes.narrowed_vocabulary(question, params, lookup)
                if narrowed: raise store.RecipeError(narrowed)
            if tier != 'recipes':   # a filter on a value the data does not hold would return an empty answer that looks real
                unknown = recipes.unknown_filter_values(sql, params, lookup)
                if unknown: raise store.RecipeError(unknown)
                whole = recipes.needs_whole_stays(question, sql)
                if whole: raise store.RecipeError(whole)
                broken = governed.check(question, sql, params)   # a reading the governed definitions rule out never answers
                if broken: raise store.RecipeError(' '.join(broken))
            if read:
                columns, rows, validation, read_steps, evidence['documents'] = reader.execute(
                    question, sql, params, read, db, readers=readers, s1_ledger=s1_ledger)
                steps.extend(read_steps)
            else:
                columns, rows = store.run(sql, params, db)
        except store.RecipeError as exc:
            steps.append({'tier': tier, 'outcome': 'execution_error', 'reason': str(exc)[:200], 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': tier, 'reason': 'execution error: ' + str(exc)[:160]})
            return None
        if not read: validation = recipes.validate(question, sql, params, columns, rows, grain=grain, total=total, db=db)
        steps.append({'tier': tier, 'outcome': 'validated' if validation['ok'] else 'validation_failed', 'tokens': 0, 'usd': 0.0,
                      'seconds': round(time.monotonic() - t0, 3)})
        if not validation['ok']:
            escalations.append({'from': tier, 'reason': 'validation failed: ' + ', '.join(c['name'] for c in validation['checks'] if not c['ok'])})
            return columns, rows, validation, False
        return columns, rows, validation, True

    # A question about a year the warehouse holds nothing for is declined before any rung: an empty count would look like an answer.
    missing_years, held = recipes.years_without_data(question, lookup)
    if missing_years:
        steps.append({'tier': 'scope', 'outcome': 'no_data_for_year', 'years': missing_years, 'tokens': 0, 'usd': 0.0})
        escalations.append({'from': 'scope', 'reason': f"no records for {', '.join(missing_years)}; the data covers {held[0]} to {held[-1]}" if held else 'no records for that year'})
        return done('unanswered', None)

    # Tier 0: bottles
    if 'bottles' in tiers and bottles is not None:
        hit = bottles.match(question, statuses=bottle_statuses)  # by default a provisional bottle is never used to answer
        if hit:
            bottle, params = hit
            got = attempt('bottles', bottle['sql'], params, grain=bottle['grain'], read=bottle.get('read'))
            if got and got[3]:
                bottles.used(bottle['id'], question, params)
                return done('answered', 'bottles', {'bottle_id': bottle['id'], 'sql': bottle['sql'], 'params': params,
                                                    **({'read': bottle['read']} if bottle.get('read') else {})}, *got[:3])
            if got and bottle.get('read') and evidence.get('documents'):  # the documents were read; undecided ones make it partial, never a second reading
                return done('partial', 'bottles', {'bottle_id': bottle['id'], 'sql': bottle['sql'], 'params': params, 'read': bottle['read']}, *got[:3])
            steps[-1]['bottle_id'] = bottle['id'] if steps else None
        else:
            steps.append({'tier': 'bottles', 'outcome': 'no_match', 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': 'bottles', 'reason': 'no bottle matches this question shape'})

    def run_recipe(tier, recipe_id, params):
        recipe = recipes.RECIPES[recipe_id]
        if tier != 'recipes':   # a model chose this recipe: it must read every table or column the question names
            from . import near
            whole = recipes.needs_whole_stays(question, recipe['sql'])
            if whole:
                steps.append({'tier': tier, 'outcome': 'recipe_does_not_count_whole_stays', 'recipe': recipe_id, 'tokens': 0, 'usd': 0.0})
                escalations.append({'from': tier, 'reason': whole})
                return None
            broken = governed.check(question, recipe['sql'], params)
            if broken:
                steps.append({'tier': tier, 'outcome': 'recipe_contradicts_a_governed_definition', 'recipe': recipe_id, 'reason': ' '.join(broken)[:200], 'tokens': 0, 'usd': 0.0})
                escalations.append({'from': tier, 'reason': ' '.join(broken)[:160]})
                return None
            missing = near.named_but_unused(question, recipe, params)
            if missing:
                steps.append({'tier': tier, 'outcome': 'recipe_does_not_read_what_the_question_names', 'recipe': recipe_id, 'names': missing, 'tokens': 0, 'usd': 0.0})
                escalations.append({'from': tier, 'reason': 'the chosen recipe does not read ' + ', '.join(missing)})
                return None
        if recipe.get('read'):
            got = attempt(tier, recipe['sql'], params, read=recipe['read'])
            if got is None: return None
            steps[-1].update({'recipe': recipe_id, 'chosen_by': tier})
            return done('answered' if got[3] else 'partial', tier,
                        {'recipe_id': recipe_id, 'sql': recipe['sql'], 'params': params, 'read': recipe['read']}, *got[:3])
        t0 = time.monotonic()
        try:
            sql, columns, rows = recipes.execute(recipe_id, params, db)
        except store.RecipeError as exc:
            steps.append({'tier': tier, 'outcome': 'execution_error', 'reason': str(exc)[:200], 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': tier, 'reason': 'execution error'})
            return None
        if not rows and recipe.get('empty'):   # a recipe may declare that no rows is itself the supported answer (an empty call queue)
            rows = [[recipe['empty']] + [''] * (len(columns) - 1)]
        validation = recipes.validate(question, sql, params, columns, rows, grain=recipe.get('grain'), total=recipe.get('total'), db=db, nullable=recipe.get('nullable', ()))
        steps.append({'tier': 'recipes', 'outcome': 'validated' if validation['ok'] else 'validation_failed', 'recipe': recipe_id,
                      'chosen_by': tier, 'tokens': 0, 'usd': 0.0, 'seconds': round(time.monotonic() - t0, 3)})
        if validation['ok']:
            return done('answered', tier, {'recipe_id': recipe_id, 'sql': sql, 'params': params}, columns, rows, validation)
        escalations.append({'from': tier, 'reason': 'validation failed: ' + ', '.join(c['name'] for c in validation['checks'] if not c['ok'])})
        return None

    # Tier 1: deterministic pattern match onto a recipe
    if 'recipes' in tiers:
        hit = recipes.match(question, lookup)
        if hit:
            out = run_recipe('recipes', *hit)
            if out: return out
        else:
            steps.append({'tier': 'recipes', 'outcome': 'no_match', 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': 'recipes', 'reason': 'no recipe pattern matches the wording'})

    # Tier 2 (System One picks the shape) then Tier 3 (small model fills its parameters)
    chosen = None
    if 'system1' in tiers and 'recipes' in tiers:
        chosen, confidence, step = models.s1_classify(question, s1_ledger)
        steps.append(step)
        if chosen is None:
            escalations.append({'from': 'system1', 'reason': step['outcome'] + f" (confidence {step.get('confidence')})"})
    if 'slm' in tiers and 'recipes' in tiers and spent() < token_ceiling:
        if chosen is not None:
            try:
                params, step = models.slm_slots(question, chosen, lookup)
            except store.RecipeError as exc:   # the recipe's vocabulary cannot be read on this warehouse: the rung does not apply
                params, step = None, {'tier': 'slm', 'outcome': 'not_applicable', 'reason': str(exc)[:160], 'tokens': 0, 'usd': 0.0}
            steps.append(step)
            if params is not None:
                fits, step = models.s1_fit(question, chosen, params, s1_ledger)
                steps.append(step)
                if fits:
                    out = run_recipe('slm', chosen, params)
                    if out: return out
                else:
                    escalations.append({'from': 'system1', 'reason': f"chosen recipe is not an exact fit (confidence {step.get('confidence')})"})
            else:
                escalations.append({'from': 'slm', 'reason': step['outcome'] + ': ' + str(step.get('reason'))[:120]})
        elif 'system1' not in tiers:
            rid, params, step = models.slm_choose_and_fill(question, lookup)
            steps.append(step)
            if rid is not None:
                out = run_recipe('slm', rid, params)
                if out: return out
            else:
                escalations.append({'from': 'slm', 'reason': step['outcome'] + ': ' + str(step.get('reason'))[:120]})

    # Near match (near.py): a promoted saved shape answers a rewording of its question, with no frontier call
    if 'near' in tiers and bottles is not None and spent() < token_ceiling:
        from . import near
        if near.enabled():
            try:
                bottle, params, near_steps = near.propose(question, bottles, s1_ledger)
            except Exception as exc:
                if 'limit' in str(exc).lower(): raise
                bottle, params, near_steps = None, None, [{'tier': 'near', 'outcome': 'error', 'reason': f'{type(exc).__name__}: {str(exc)[:120]}', 'tokens': 0, 'usd': 0.0}]
            steps.extend(near_steps)
            if bottle is not None:
                got = attempt('near', bottle['sql'], params, grain=bottle['grain'], read=bottle.get('read'))
                if got and got[3]:
                    bottles.used(bottle['id'], question, params, via='near')
                    return done('answered', 'near', {'bottle_id': bottle['id'], 'sql': bottle['sql'], 'params': params, 'near_match_of': bottle['template'],
                                                     **({'read': bottle['read']} if bottle.get('read') else {})}, *got[:3])
                escalations.append({'from': 'near', 'reason': 'the saved shape ran but its result did not pass the checks'})
            else:
                escalations.append({'from': 'near', 'reason': str(near_steps[-1].get('outcome'))})

    # Tier 4: the frontier model writes a recipe; code executes and validates it
    if 'llm' not in tiers:
        return done('unanswered', None, missing='No enabled tier produced a validated answer')
    if spent() >= token_ceiling:
        return done('partial', None, missing=f'Token ceiling {token_ceiling} reached before the frontier model was called')
    recipe, step = models.llm_recipe(question)
    steps.append(step)
    if recipe is None and step.get('outcome') == 'cannot_answer':
        escalations.append({'from': 'llm', 'reason': 'one query over the semantic tables cannot answer this: ' + str(step.get('reason'))[:200]})
        return done('unanswered', None, missing=step.get('reason'))
    if recipe is None:
        return done('partial', 'llm', missing='The frontier model returned no usable recipe: ' + str(step.get('reason')))
    params = {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in recipe['params'].items()}
    got = attempt('llm', recipe['sql'], params, grain=recipe.get('grain') or None, read=recipe.get('read'))
    if got is None and spent() < token_ceiling:  # one correction for a SQL execution error; validation failures are not retried
        recipe2, step2 = models.llm_recipe(question, error=steps[-1].get('reason'))
        steps.append({**step2, 'retry': 'execution_error'})
        if recipe2 is not None:
            recipe = recipe2
            params = {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in recipe['params'].items()}
            got = attempt('llm', recipe['sql'], params, grain=recipe.get('grain') or None, read=recipe.get('read'))
    if got is None:
        return done('partial', 'llm', recipe, missing='The frontier model recipe did not execute')
    columns, rows, validation, ok = got
    extra = {}
    if ok and bottle_new and bottles is not None:
        bottle_id, why = bottles.add(question, recipe, validation, {'tier': 'llm', 'model': models.LLM, 'question': question})
        extra['bottled'] = bottle_id if bottle_id else {'not_bottled': why}
    return done('answered' if ok else 'partial', 'llm', recipe, columns, rows, validation, **extra)
