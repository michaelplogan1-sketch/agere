-- One row per source chart (patient_id). person_id is the MPI-resolved person
-- (intermediate/int_person_match.sql). Demographics come from the roster.
    select
        m.person_id
      , p.patient_id
      , split_part(p.display_name, ' ', 1)                         as first_name
      , nullif(trim(substr(p.display_name, length(split_part(p.display_name, ' ', 1)) + 2)), '') as last_name
      , case upper(p.sex_reported) when 'F' then 'female' when 'M' then 'male' else 'unknown' end as sex
      , lower(p.race)                                               as race
      , lower(p.ethnicity)                                          as ethnicity
      , p.birth_date
      , d.death_date
      , case when d.death_date is not null then 1 else 0 end       as death_flag
      , p.address_line                                              as address
      , p.city
      , p.state
      , p.postal_code                                               as zip_code
      , p.county_fips                                               as county
      , p.phone
      , cast(null as timestamp)                                     as ingest_datetime
      , 'patient_roster'                                            as data_source
      , m.mpi_status                                                as x_mpi_status
      , p.preferred_language                                        as x_preferred_language
      , p.marital_status                                            as x_marital_status
      , p.data_segment                                              as x_data_segment
      , d.death_source                                              as x_death_source
    from {{ ref('stg_raw__patients') }} p
    join {{ ref('int_person_match') }} m on m.patient_id = p.patient_id
    left join {{ ref('int_person_death') }} d on d.patient_id = p.patient_id
