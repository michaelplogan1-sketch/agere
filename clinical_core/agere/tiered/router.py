"""The router: code plus Tier 2. A question starts at Tier 0 and climbs one rung only when the current rung
does not apply, is not confident, or its output fails deterministic validation. Every escalation is recorded
with its reason. The frontier model is the last rung; its answer is returned even when validation fails,
labelled partial with the failed checks."""
import time

from . import bottles as bottle_store, models, recipes, store

ALL_TIERS = ('bottles', 'recipes', 'system1', 'slm', 'llm')
TOKEN_CEILING = 60000


def answer(question, *, tiers=ALL_TIERS, bottles=None, bottle_new=False, token_ceiling=TOKEN_CEILING, s1_ledger=None):
    """-> {'status','final_tier','columns','rows','recipe','validation','steps','seconds','escalations'}."""
    started = time.monotonic()
    steps, escalations = [], []
    db = store.connect()
    def spent(): return sum(s.get('tokens', 0) for s in steps)
    def done(status, tier, recipe=None, columns=None, rows=None, validation=None, **extra):
        db.close()
        return {'status': status, 'final_tier': tier, 'recipe': recipe, 'columns': columns, 'rows': rows, 'validation': validation,
                'steps': steps, 'escalations': escalations, 'seconds': round(time.monotonic() - started, 2), **extra}
    def attempt(tier, sql, params, grain=None, total=None):
        """Execute and validate one proposal. -> (columns, rows, validation) or None with the escalation recorded."""
        t0 = time.monotonic()
        try:
            columns, rows = store.run(sql, params, db)
        except store.RecipeError as exc:
            steps.append({'tier': tier, 'outcome': 'execution_error', 'reason': str(exc)[:200], 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': tier, 'reason': 'execution error: ' + str(exc)[:160]})
            return None
        validation = recipes.validate(question, sql, params, columns, rows, grain=grain, total=total, db=db)
        steps.append({'tier': tier, 'outcome': 'validated' if validation['ok'] else 'validation_failed', 'tokens': 0, 'usd': 0.0,
                      'seconds': round(time.monotonic() - t0, 3)})
        if not validation['ok']:
            escalations.append({'from': tier, 'reason': 'validation failed: ' + ', '.join(c['name'] for c in validation['checks'] if not c['ok'])})
            return columns, rows, validation, False
        return columns, rows, validation, True

    # Tier 0: bottles
    if 'bottles' in tiers and bottles is not None:
        hit = bottles.match(question, statuses=('promoted',))  # a provisional bottle is never used to answer
        if hit:
            bottle, params = hit
            got = attempt('bottles', bottle['sql'], params, grain=bottle['grain'])
            if got and got[3]:
                bottles.used(bottle['id'])
                return done('answered', 'bottles', {'bottle_id': bottle['id'], 'sql': bottle['sql'], 'params': params}, *got[:3])
        else:
            steps.append({'tier': 'bottles', 'outcome': 'no_match', 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': 'bottles', 'reason': 'no bottle matches this question shape'})

    def run_recipe(tier, recipe_id, params):
        recipe = recipes.RECIPES[recipe_id]
        t0 = time.monotonic()
        try:
            sql, columns, rows = recipes.execute(recipe_id, params, db)
        except store.RecipeError as exc:
            steps.append({'tier': tier, 'outcome': 'execution_error', 'reason': str(exc)[:200], 'tokens': 0, 'usd': 0.0})
            escalations.append({'from': tier, 'reason': 'execution error'})
            return None
        validation = recipes.validate(question, sql, params, columns, rows, grain=recipe.get('grain'), total=recipe.get('total'), db=db)
        steps.append({'tier': 'recipes', 'outcome': 'validated' if validation['ok'] else 'validation_failed', 'recipe': recipe_id,
                      'chosen_by': tier, 'tokens': 0, 'usd': 0.0, 'seconds': round(time.monotonic() - t0, 3)})
        if validation['ok']:
            return done('answered', tier, {'recipe_id': recipe_id, 'sql': sql, 'params': params}, columns, rows, validation)
        escalations.append({'from': tier, 'reason': 'validation failed: ' + ', '.join(c['name'] for c in validation['checks'] if not c['ok'])})
        return None

    # Tier 1: deterministic pattern match onto a recipe
    if 'recipes' in tiers:
        hit = recipes.match(question, db)
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
            params, step = models.slm_slots(question, chosen, db)
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
            rid, params, step = models.slm_choose_and_fill(question, db)
            steps.append(step)
            if rid is not None:
                out = run_recipe('slm', rid, params)
                if out: return out
            else:
                escalations.append({'from': 'slm', 'reason': step['outcome'] + ': ' + str(step.get('reason'))[:120]})

    # Tier 4: the frontier model writes a recipe; code executes and validates it
    if 'llm' not in tiers:
        return done('unanswered', None, missing='No enabled tier produced a validated answer')
    if spent() >= token_ceiling:
        return done('partial', None, missing=f'Token ceiling {token_ceiling} reached before the frontier model was called')
    recipe, step = models.llm_recipe(question)
    steps.append(step)
    if recipe is None:
        return done('partial', 'llm', missing='The frontier model returned no usable recipe: ' + str(step.get('reason')))
    params = {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in recipe['params'].items()}
    got = attempt('llm', recipe['sql'], params, grain=recipe.get('grain') or None)
    if got is None and spent() < token_ceiling:  # one correction for a SQL execution error; validation failures are not retried
        recipe2, step2 = models.llm_recipe(question, error=steps[-1].get('reason'))
        steps.append({**step2, 'retry': 'execution_error'})
        if recipe2 is not None:
            recipe = recipe2
            params = {k: (int(v['value']) if v.get('type') == 'int' else str(v.get('value'))) for k, v in recipe['params'].items()}
            got = attempt('llm', recipe['sql'], params, grain=recipe.get('grain') or None)
    if got is None:
        return done('partial', 'llm', recipe, missing='The frontier model recipe did not execute')
    columns, rows, validation, ok = got
    extra = {}
    if ok and bottle_new and bottles is not None:
        bottle_id, why = bottles.add(question, recipe, validation, {'tier': 'llm', 'model': models.LLM, 'question': question})
        extra['bottled'] = bottle_id if bottle_id else {'not_bottled': why}
    return done('answered' if ok else 'partial', 'llm', recipe, columns, rows, validation, **extra)
