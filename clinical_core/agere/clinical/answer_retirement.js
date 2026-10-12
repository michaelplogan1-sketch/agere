// Explicit local work-context changes. Text stays text; saved history is retained.
function addAnswerRetirementControls(parent, item) {
  const section = N('details', undefined, 'rule');
  section.append(N('summary', 'Manage attached machine answers'));
  const body = N('div');
  section.append(body);
  parent.append(section);
  let loaded = false;
  section.addEventListener('toggle', () => {
    if (!section.open || loaded) return;
    action(async () => {
      const state = await api('/api/stack/work/answers?item=' + encodeURIComponent(item.id));
      if (state.revision !== item.revision) throw Error('Work changed. Reload this patient before managing answers.');
      body.replaceChildren(N('p', 'Retirement changes which machine answers this work uses. Original answers and history remain available. A local operational record does not establish clinician review or authorize treatment.', 'muted'));
      const selectedAnswers = [];
      for (const answer of state.answers) {
        const label = N('label'), check = N('input');
        check.type = 'checkbox'; check.value = answer.answer_id;
        check.setAttribute('aria-label', 'Retire answer ' + answer.answer_id);
        label.append(check, N('span', answer.question || answer.answer_id));
        const link = N('a', 'Open original answer');
        link.href = '/stack?case=' + encodeURIComponent(answer.answer_id);
        label.append(N('p', answer.current ? 'Dependencies current' : 'Dependencies need attention: ' + answer.reason, answer.current ? 'muted' : 'warn'), link);
        body.append(label); selectedAnswers.push(check);
      }
      if (state.answers.length) {
        const form = N('form');
        const reason = field(form, 'Reason for retirement', 'select', ['withdrawn_for_review', 'stale_dependencies', 'replaced_for_review', 'outside_work_scope']);
        const replacements = field(form, 'Replacement saved answer IDs (optional, separated by commas)', 'textarea');
        replacements.placeholder = 'Paste exact saved answer IDs for this patient';
        const note = field(form, 'Explain the change', 'textarea'); note.required = true; note.maxLength = 3000;
        const actor = field(form, 'Recorded by (local operational label)'); actor.required = true; actor.maxLength = 120;
        actor.value = sessionStorage.getItem('agere-work-actor') || '';
        const preview = N('button', 'Preview answer retirement'); preview.type = 'submit'; preview.style.marginTop = '12px';
        const result = N('div'); result.setAttribute('aria-live', 'polite');
        let requestVersion = 0;
        const invalidate = () => { requestVersion++; result.replaceChildren(); };
        form.addEventListener('input', invalidate);
        for (const control of selectedAnswers) control.addEventListener('change', invalidate);
        form.append(preview, result); body.append(form);
        form.onsubmit = event => {
          event.preventDefault();
          action(async () => {
            const generation = ++requestVersion;
            const answerIds = selectedAnswers.filter(x => x.checked).map(x => x.value);
            if (!answerIds.length) throw Error('Select at least one attached answer to retire.');
            const recordedActor = actor.value;
            const plan = await api('/api/stack/work/answers/prepare', {
              item_id: item.id, expected_revision: item.revision, answer_ids: answerIds,
              replacement_ids: replacements.value.split(',').map(x => x.trim()).filter(Boolean),
              reason: reason.value, note: note.value
            });
            if (generation !== requestVersion) return;
            result.replaceChildren(N('h3', 'Review the proposed change'), N('p',
              plan.removed_answer_ids.length + ' to retire · ' + plan.retained_answer_ids.length + ' retained · ' +
              plan.replacement_answer_ids.length + ' replacement selections. No work has changed yet.'));
            result.append(N('p', 'Saving requires reassessment before this work advances. ' +
              plan.downstream_tasks.length + (plan.downstream_tasks.length === 1 ? ' current buyer task references an affected assessment and will require a fresh plan.' : ' current buyer tasks reference affected assessments and will require fresh plans.'), 'notice warn'));
            result.append(detailJSON('Exact answers, reason and downstream impact', plan));
            const save = N('button', 'Record retirement and require reassessment', 'primary'); save.type = 'button';
            const requestId = crypto.randomUUID();
            save.onclick = () => action(async () => {
              if (generation !== requestVersion) throw Error('Inputs changed. Preview the change again.');
              await api('/api/stack/work/answers/apply', {plan_id: plan.id, actor: recordedActor, request_id: requestId});
              sessionStorage.setItem('agere-work-actor', recordedActor);
              $('status').textContent = 'Answer retirement recorded. Original history retained; reassessment is required.';
              await openItem(item.id);
            });
            result.append(save);
          });
        };
      } else body.append(N('p', 'No machine answers are attached to the current assessment.'));
      body.append(N('h3', 'Retirement history'));
      if (!state.retirements.length) body.append(N('p', 'No answer retirements recorded for this work item.', 'muted'));
      for (const entry of [...state.retirements].reverse()) {
        body.append(N('p', pretty(entry.reason) + ' · revision ' + entry.after_revision + ' · ' + entry.actor),
          N('p', entry.note), detailJSON('Immutable retirement record', entry));
      }
      loaded = true;
    });
  });
}
