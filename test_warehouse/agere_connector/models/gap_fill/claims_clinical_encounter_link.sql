-- Gap filler: links each Tuva claims-derived encounter (core.encounter,
-- encounter_source_type = 'claim') to the clinical encounter (EHR or HL7 ADT)
-- of the same visit. Tuva keeps both and does not link them.
--
-- Match rule (same person_id, then by setting):
--   inpatient  : claims encounter_group 'inpatient' <-> clinical 'inpatient' group;
--                stays overlap with one day of slack on each side
--                (claim start <= clinical end + 1 and claim end >= clinical start - 1).
--   emergency  : claims 'emergency department' <-> clinical 'emergency department'
--                starting within 1 day, or a clinical acute inpatient stay that
--                started within 1 day (ED visit that led to the admission).
--   other      : claims office-based / outpatient <-> clinical encounter of the
--                same encounter_group starting within 1 day.
-- Among candidates the best match is the same encounter_type first, then the
-- smallest start-date gap, then the clinical encounter id (deterministic). Each
-- claims encounter gets at most one link (match_rank = 1 rows only); a clinical
-- encounter may link to several claims encounters (e.g. facility and
-- professional claims grouped separately).
with claims as (
    select encounter_id, person_id, encounter_type, encounter_group, encounter_start_date, encounter_end_date, data_source
    from {{ ref('core__encounter') }}
    where encounter_source_type = 'claim'
),
clinical as (
    select encounter_id, person_id, encounter_type, encounter_group, encounter_start_date,
           coalesce(encounter_end_date, encounter_start_date) as encounter_end_date, data_source
    from {{ ref('core__encounter') }}
    where encounter_source_type = 'clinical'
),
candidates as (
    select
        c.encounter_id                    as claims_encounter_id
      , c.data_source                     as claims_data_source
      , c.person_id
      , c.encounter_type                  as claims_encounter_type
      , c.encounter_start_date            as claims_start_date
      , k.encounter_id                    as clinical_encounter_id
      , k.data_source                     as clinical_data_source
      , k.encounter_type                  as clinical_encounter_type
      , k.encounter_start_date            as clinical_start_date
      , case
          when c.encounter_group = 'inpatient' then 'inpatient_overlap'
          when c.encounter_type = 'emergency department' and k.encounter_type = 'emergency department' then 'ed_same_visit'
          when c.encounter_type = 'emergency department' then 'ed_to_admission'
          else 'same_group_within_1_day'
        end                               as match_rule
      , abs(datediff('day', c.encounter_start_date, k.encounter_start_date)) as start_gap_days
      , case when c.encounter_type = k.encounter_type then 0 else 1 end as type_mismatch
    from claims c
    join clinical k
      on k.person_id = c.person_id
     and (
          (c.encounter_group = 'inpatient' and k.encounter_group = 'inpatient'
           and c.encounter_start_date <= k.encounter_end_date + 1
           and coalesce(c.encounter_end_date, c.encounter_start_date) >= k.encounter_start_date - 1)
       or (c.encounter_type = 'emergency department'
           and k.encounter_type in ('emergency department', 'acute inpatient')
           and abs(datediff('day', c.encounter_start_date, k.encounter_start_date)) <= 1)
       or (c.encounter_group <> 'inpatient' and c.encounter_type <> 'emergency department'
           and k.encounter_group = c.encounter_group
           and abs(datediff('day', c.encounter_start_date, k.encounter_start_date)) <= 1)
     )
)
select
    *
  , count(*) over (partition by claims_encounter_id) as candidate_count
from candidates
qualify row_number() over (
    partition by claims_encounter_id
    order by type_mismatch, start_gap_days, clinical_encounter_id) = 1
