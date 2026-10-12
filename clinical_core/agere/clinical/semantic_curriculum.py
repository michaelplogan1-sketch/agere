"""Authored synthetic source-language curriculum, never clinician-reviewed truth.

Three sibling paraphrases exercise the software partitions. They are not an
independent clinical validation corpus and cannot authorize model promotion.
"""
from ...contracts import digest

VERSION = 'authored-source-language/1'


def build():
    # Each recipe: statement, time, relation, concept kind, three distinct texts.
    recipes = [
      ('patient_affirmed','current_at_source','prescription_order','drug',
       'Today a prescription for {x} was ordered.', 'A new order for {x} was signed at this encounter.', 'At this visit the clinician issued a prescription for {x}.'),
      ('patient_affirmed','current_at_source','dispense_record','drug',
       'The pharmacy dispensed {x} today.', 'A fill of {x} is documented for this date.', 'Today the pharmacy supplied {x} to the patient.'),
      ('patient_affirmed','current_at_source','reported_use','drug',
       'Today the patient confirms taking {x} every day.', 'At this encounter she reports that she is using {x}.', 'He says he is currently taking {x} as prescribed.'),
      ('patient_affirmed','current_at_source','administration','drug',
       'The nurse administered {x} today.', 'The administration record confirms that {x} was given at this encounter.', 'At this visit a dose of {x} was administered to the patient.'),
      ('patient_affirmed','current_at_source','hold_stop','drug',
       'Today the patient stopped {x}.', 'At this visit she confirms {x} has been discontinued.', 'He reports that {x} is now on hold.'),
      ('patient_affirmed','current_at_source','dose_change','drug',
       'Today the patient declined a dose increase of {x}; the existing dose continues.', 'At this encounter she refused titration of {x}, while continuing the same dose.', 'He declined increasing {x} at this visit; he has not stopped the drug.'),
      ('patient_affirmed','planned','care_plan','care',
       'A referral for {x} is planned for next month.', 'We will arrange {x} at a future visit.', 'The plan is to schedule {x} next week.'),
      ('patient_affirmed','current_at_source','care_completion','care',
       'The patient attended {x} today.', 'At this encounter the patient completed {x}.', 'Today the record confirms attendance at {x}.'),
      ('patient_affirmed','current_at_source','record_request','care',
       'Today we requested records about {x}.', 'At this visit a request for the {x} documents was sent.', 'The team asked for the outside {x} report today.'),
      ('patient_affirmed','current_at_source','source_coverage','care',
       'Records about {x} are currently unavailable to this clinic.', 'Our system has no access to outside {x} documentation today.', 'At this encounter the external {x} data feed is missing.'),
      ('patient_affirmed','current_at_source','clinical_observation','barrier',
       'Today the patient reports {x}.', 'At this visit she confirms experiencing {x}.', 'He describes {x} as a current problem.'),
      ('patient_negated','current_at_source','clinical_observation','barrier',
       'Today the patient denies {x}.', 'At this visit she explicitly reports no {x}.', 'He says he is not experiencing {x} at present.'),
      ('patient_negated','current_at_source','reported_use','drug',
       'Today the patient explicitly denies taking {x}.', 'At this encounter she says she is not using {x}.', 'He reports no current use of {x}.'),
      ('patient_negated','current_at_source','care_completion','care',
       'The patient says {x} has not been completed as of today.', 'At this visit she denies having attended {x}.', 'He confirms today that he did not complete {x}.'),
      ('patient_affirmed','historical','reported_use','drug',
       'Five years ago the patient took {x}; current use is not described.', 'An old account states she used {x} in the past, without reconfirmation.', 'A prior-year note records use of {x}; this is copied history only.'),
      ('patient_affirmed','historical','hold_stop','drug',
       'A prior note says {x} was stopped years ago.', 'Copied history: the patient discontinued {x} in a previous year.', 'In the remote past she held {x}, according to an older account.'),
      ('patient_affirmed','historical','care_completion','care',
       'Two years ago the patient completed {x}.', 'An older account describes attendance at {x} in the past.', 'Copied prior-year history documents completion of {x}.'),
      ('patient_affirmed','historical','clinical_observation','barrier',
       'Last year the patient reported {x}.', 'A remote history includes {x}; it was not reconfirmed.', 'In a prior encounter she described {x}.'),
      ('patient_affirmed','planned','prescription_order','drug',
       'The clinician plans to prescribe {x} next month.', 'A prescription for {x} will be ordered next week.', 'At a future encounter we intend to issue an order for {x}.'),
      ('patient_affirmed','planned','dose_change','drug',
       'The plan is to increase the dose of {x} next month.', 'Next week a titration of {x} is scheduled.', 'A future dose adjustment of {x} is planned.'),
      ('other_person','current_at_source','reported_use','drug',
       'Her father currently takes {x}; the patient is not the subject.', 'His sister reports using {x} today.', 'The spouse confirms daily use of {x} at present.'),
      ('other_person','historical','clinical_observation','barrier',
       'Her mother had {x} years ago.', 'His brother previously experienced {x}.', 'The spouse described {x} in a remote account.'),
      ('hypothetical','planned','care_plan','care',
       'If symptoms develop, {x} might be considered in the future.', 'We may arrange {x} next month if the condition worsens.', 'Future {x} would be considered only if a new problem occurs.'),
      ('hypothetical','planned','hold_stop','drug',
       'If problems occur, {x} might be stopped next week.', 'We could hold {x} in the future if symptoms arise.', 'Discontinuation of {x} may be considered later, only if needed.'),
      ('retracted','unspecified','reported_use','drug',
       'The statement that the patient takes {x} was entered in error and is withdrawn.', 'Correction: the report of use of {x} is retracted; no timing was supplied.', 'The account of taking {x} is explicitly withdrawn as erroneous.'),
      ('nonclinical','unspecified','unknown','drug',
       'Template instruction: insert {x} into the blank. This is not a patient account.', 'Software example only: {x}. Ignore this placeholder as a clinical statement.', 'Metadata label for the editor: {x}; no patient event is described.'),
      ('unknown','unknown','reported_use','drug',
       'One undated account says the patient takes {x}; another denies it. Subject and timing cannot be reconciled.', 'Use of {x} is both affirmed and denied in conflicting accounts with unclear dates.', 'Contradictory statements about taking {x} lack a resolvable chronology.'),
      ('patient_affirmed','unspecified','clinical_observation','barrier',
       'The patient reports {x}; the source does not say when.', 'An undated account describes {x} for this patient.', 'A report of {x} concerns the patient, but contains no usable time.'),
      ('patient_affirmed','unspecified','unknown','drug',
       '{x} is mentioned in a patient list without an event, usage, or date.', 'An undated patient list contains {x}; the relationship is not explained.', 'Patient entry: {x}. There is no description of its role or timing.'),
      ('patient_affirmed','current_at_source','reported_use','drug',
       'Today a different medication was stopped. The patient continues taking {x}.', 'At this visit she confirms use of {x}; the discontinuation concerns another drug.', 'He is currently taking {x}. The neighboring stop instruction names a different medication.'),
      ('patient_affirmed','current_at_source','clinical_observation','barrier',
       'No transportation issue today, but the patient confirms {x}.', 'At this visit she denies a housing problem. She does report {x}.', 'He has no food access problem currently; however he describes {x}.'),
    ]
    entities = {
        'drug':['metoprolol','carvedilol','metformin','lisinopril','losartan','furosemide'],
        'care':['nephrology consultation','cardiology review','eye examination','physical therapy visit','nutrition consultation','pulmonary assessment'],
        'barrier':['difficulty affording medications','financial strain','trouble paying medical bills','an inability to afford prescriptions','financial hardship','difficulty covering copayments']}
    rows=[]
    for i,(statement,time,relation,kind,*phrases) in enumerate(recipes):
        for part,phrase in zip(('train','calibration','validation'),phrases):
            family=f'{VERSION}:{part}:{i}'
            for entity in entities[kind]:
                text=phrase.format(x=entity);a=text.index(entity)
                context={'synthetic':True,'concept':entity,'text':text,'target_start':a,'target_end':a+len(entity),
                    'target_quote':entity,'context_complete':True,'window_start':0,'window_end':len(text),
                    'omitted_character_ranges':[],'source_date':None,'available_date':None,
                    'literal_reference_date':None,'reference_status':'no_explicit_date','copied_history':False,'warehouse_cutoff':None}
                rows.append({'id':digest([family,entity]),'input':context,'labels':{'statement':statement,'time':time,'relation':relation},
                    'partition':part,'patient_family':family,'source_families':[digest(phrase)],'source_hash':digest(text),
                    'supervision':'Authored synthetic development labels; not clinician-reviewed','clinical_judgment_label':False})
    return rows
