-- Tuva medication (clinical).
--   medication_orders          : prescriptions/orders (RxNorm), prescribing_date = order start.
--                                Superseded versions are dropped; an order that replaces
--                                an earlier order (renewal / dose change) is a new
--                                prescribing event, so both are kept.
--   medication_administrations : facility administrations with status given.
--   pharmacy_dispenses         : owned-pharmacy dispenses with status dispensed
--                                (prepared and returned_to_stock are not fills);
--                                dispensing_date, NDC + RxNorm.
with superseded as (
    select parent_record_id from {{ ref('stg_raw__supersession') }}
    where stream in ('medication_orders', 'medication_administrations', 'pharmacy_dispenses') and link_type = 'supersedes'
),
orders as (
    select record_id as medication_id, patient_id, {{ js('encounter_id') }} as encounter_id,
           cast(null as date) as dispensing_date, coalesce({{ jdate('start_time') }}, event_date) as prescribing_date,
           'rxnorm' as source_code_type, {{ js('rxcui') }} as source_code, {{ js('medication_name') }} as source_description,
           cast(null as varchar) as ndc_code, {{ js('rxcui') }} as rxnorm_code, {{ js('route') }} as route,
           coalesce({{ js('strength') }} || ' ' || {{ js('strength_unit') }}, {{ js('formulation') }}) as strength,
           coalesce({{ jnum('quantity_ordered') }}, {{ jnum('quantity') }}) as quantity, {{ js('quantity_unit') }} as quantity_unit,
           {{ jnum('days_supply') }} as days_supply, {{ js('ordering_clinician_id') }} as practitioner_id,
           'medication_orders' as source_stream, coalesce({{ js('order_status') }}, {{ js('status') }}) as status,
           {{ js('source_system') }} as source_system, arrival_date
    from {{ stream('medication_orders') }}
    where record_id not in (select parent_record_id from superseded)
),
admin as (
    select record_id, patient_id, {{ js('encounter_id') }}, event_date, cast(null as date),
           'rxnorm', {{ js('rxcui') }}, {{ js('medication_name') }}, cast(null as varchar), {{ js('rxcui') }}, {{ js('route') }},
           {{ js('dose_given') }} || ' ' || {{ js('dose_unit') }}, {{ jnum('dose_given') }}, {{ js('dose_unit') }}, cast(null as double),
           cast(null as varchar), 'medication_administrations', {{ js('administration_status') }}, {{ js('source_system') }}, arrival_date
    from {{ stream('medication_administrations') }}
    where record_id not in (select parent_record_id from superseded) and {{ js('administration_status') }} = 'given'
),
disp as (
    select record_id, patient_id, {{ js('encounter_id') }}, event_date, cast(null as date),
           case when (j ->> 'ndc') is not null then 'ndc' else 'rxnorm' end, coalesce({{ js('ndc') }}, {{ js('rxcui') }}),
           {{ js('medication_name') }}, {{ js('ndc') }}, {{ js('rxcui') }}, cast(null as varchar),
           cast(null as varchar), {{ jnum('quantity') }}, {{ js('quantity_unit') }}, {{ jnum('days_supply_reported') }},
           cast(null as varchar), 'pharmacy_dispenses', {{ js('status') }}, {{ js('source_system') }}, arrival_date
    from {{ stream('pharmacy_dispenses') }}
    where record_id not in (select parent_record_id from superseded) and {{ js('status') }} = 'dispensed'
),
u as (select * from orders union all select * from admin union all select * from disp)
select m.person_id, u.* exclude (source_stream, status, source_system, arrival_date),
       case when u.source_stream = 'pharmacy_dispenses' then 'pharmacy_system' else 'ehr' end as data_source,
       u.source_stream as x_source_stream, u.status as x_status, u.source_system as x_source_system,
       u.patient_id as x_patient_id, u.arrival_date as x_available_date
from u
join {{ ref('int_person_match') }} m on m.patient_id = u.patient_id
