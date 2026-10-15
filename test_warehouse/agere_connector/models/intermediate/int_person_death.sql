-- Date of death per source patient. death_records (death index / payer death
-- indicator) beat EHR demographic_updates marked deceased; earliest date wins.
with dr as (
    select patient_id, {{ jdate('date_of_death') }} as death_date, 1 as priority,
           {{ js('source') }} as death_source
    from {{ stream('death_records') }}
),
du as (
    select patient_id, {{ jdate('date_of_death') }} as death_date, 2 as priority,
           'ehr_demographic_update' as death_source
    from {{ stream('demographic_updates') }}
    where {{ js('update_type') }} = 'deceased' and (j ->> 'date_of_death') is not null
),
u as (select * from dr union all select * from du)
select patient_id, death_date, death_source
from u
where death_date is not null
qualify row_number() over (partition by patient_id order by priority, death_date) = 1
