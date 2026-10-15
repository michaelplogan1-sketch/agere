-- Master patient index. The raw roster can hold the same person under two source
-- patient ids (registration duplicates with a misspelled name). Deterministic +
-- fuzzy rule, applied to every roster pair (no source flags are used):
--   same birth_date AND same sex AND same 5-digit postal code
--   AND Jaro-Winkler(surname) >= 0.85 AND Jaro-Winkler(first name) >= 0.80
-- (tolerates transposed/missing letters and short forms such as Eli/Elijah,
-- rejects same-DOB neighbours with a different surname).
-- Each matched cluster takes one person_id: the member id with the most raw
-- records (ties -> lowest id). Unmatched patients are their own person.
with p as (
    select patient_id, lower(trim(display_name)) as nm, birth_date, sex_reported, left(postal_code, 5) as zip5
    from {{ ref('stg_raw__patients') }}
),
pairs as (
    select a.patient_id as a_id, b.patient_id as b_id,
           jaro_winkler_similarity(a.nm, b.nm) as name_similarity
    from p a
    join p b
      on a.birth_date = b.birth_date and a.sex_reported = b.sex_reported and a.zip5 = b.zip5
     and a.patient_id < b.patient_id
    where jaro_winkler_similarity(split_part(a.nm, ' ', -1), split_part(b.nm, ' ', -1)) >= 0.85
      and jaro_winkler_similarity(split_part(a.nm, ' ', 1), split_part(b.nm, ' ', 1)) >= 0.80
),
volume as (
    select patient_id, count(*) as n_records from {{ ref('stg_raw__records') }} group by 1
),
members as (
    select a_id as patient_id, a_id as pair_a, b_id as pair_b from pairs
    union all
    select b_id, a_id, b_id from pairs
),
-- Pairs are disjoint in practice (checked by the uniqueness test on patient_id);
-- a cluster is the pair.
ranked as (
    select m.patient_id, m.pair_a, m.pair_b,
           row_number() over (partition by m.pair_a, m.pair_b
                              order by coalesce(v.n_records, 0) desc, m.patient_id) as rn
    from members m left join volume v on v.patient_id = m.patient_id
),
survivor as (
    select pair_a, pair_b, patient_id as survivor_id from ranked where rn = 1
)
select
    p.patient_id
  , coalesce(s.survivor_id, p.patient_id) as person_id
  , case when s.survivor_id is null then 'unique'
         when s.survivor_id = p.patient_id then 'mpi_survivor'
         else 'mpi_merged' end as mpi_status
  , pr.name_similarity as mpi_name_similarity
from {{ ref('stg_raw__patients') }} p
left join pairs pr on p.patient_id in (pr.a_id, pr.b_id)
left join survivor s on s.pair_a = pr.a_id and s.pair_b = pr.b_id
