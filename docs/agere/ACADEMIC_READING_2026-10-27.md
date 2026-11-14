# Academic teaching for Agere learning

Mike's reference is [Gregg Fonarow's Optimal Implementation of GDMT for Heart
Failure](https://www.youtube.com/watch?v=tN0sseDAaj4), Emory Cardiology Grand
Rounds, September 22, 2025. Emory's archive and the public recording description
agree on the presenter and date. The player shows 1:05:32. The transcript button
opened but remained loading; no usable transcript or full lecture was reviewed.

The candidate reading library contains 28 new lecture records and 18 background
cards, including Harvard value-based-care material, open nursing education,
NIDDK references, two UCTV references and an ACC teaching-deck summary. These are
cited reading resources and authored analytics objectives. They are not 46
clinically adjudicated lessons or 46 completed video reviews.

## Closest matches to Mike's example

| Recording | Presenter / institution / event date | Proposed Agere use |
| --- | --- | --- |
| [The Power of Pills: Opportunities and Challenges](https://www.youtube.com/watch?v=8JLncF1IpAs&t=286s) | Michelle Kittleson / Houston Methodist DeBakey / February 29, 2024 | Medication coverage, implementation barriers and careful interpretation of trials. Publisher description and chapter headings inspected; full recording not watched. |
| [Heart Failure 2026: Update on Therapy](https://www.youtube.com/watch?v=ibEQhMPKRjo) | Clyde Yancy / Emory / March 9, 2026 | Versioned treatment knowledge, phenotype and dated evidence. |
| [Foundations of GDMT](https://mssvideoupload.mayo.edu/media/Foundations+of+GDMT/1_itu8xdri) | Melissa Lyle / Mayo / February 24, 2026 | Class coverage versus dose exposure and documented tolerance. Public guest player verified. |
| [GDMT and Confronting Clinical Inertia](https://www.youtube.com/watch?v=Zkq_72ndZbQ) | Sophia Ambruso / Colorado Anschutz / September 10, 2025 | Cross-condition care opportunities and documented barriers. |
| [Cardiorenal Syndrome](https://mssvideoupload.mayo.edu/media/Cardiorenal+Syndrome/1_46tdvtig) | Lyle Baker / Mayo / January 27, 2026 | Medication changes, dated kidney/laboratory evidence and documented reasons. |
| [When GDMT is not enough](https://mssvideoupload.mayo.edu/media/When+GDMT+is+not+enough/1_co0f26t5) | Juan Leoni / Mayo / March 31, 2026 | Admissions, treatment limitations and referral completion. |
| [Managing a Heart Failure Clinic in the Community Setting](https://mssvideoupload.mayo.edu/media/Managing+a+Heart+Failure+Clinic+in+the+Community+Setting+-+Toinetta+Coolie%2C+DNP%2C+APRN%2C+FNP-BC/1_xuvbrvvh) | Toinetta Coolie / Mayo / September 19, 2024 | Referral, enrollment, follow-up, service capacity and unresolved prerequisites. |
| [Myocardial Recovery](https://player.vimeo.com/video/1130960227) | Arvind Bhimaraj / Texas Heart Institute at Baylor / October 24, 2025 | Serial measurements, treatment exposure and longitudinal outcomes. |

Primary archives establish the titles, dates and recording links:
[Emory](https://med.aws.emory.edu/departments/medicine/divisions/cardiology/cme/cme-archives.html),
[Mayo](https://ce.mayo.edu/transplant-medicine/content/mayo-clinic-advanced-heart-failure-lecture-series),
[Colorado](https://medschool.cuanschutz.edu/medicine/calendar/grand-rounds/grand-rounds-archive),
[Texas Heart](https://www.texasheart.org/education/grand-rounds-2025-2026/).
Only the specifically identified public players were inspected. A published
recording link is recorded as such, not as proof of successful playback or
transcript access. The proposed analytics uses above are Agere-authored.

## Broader disease and healthcare context

The same source library includes Colorado's
[COPD update](https://www.youtube.com/watch?v=pwGwq0IAmWE), October 15, 2026,
[diabetes heterogeneity lecture](https://www.youtube.com/watch?v=y2uqOSF2bw4),
October 29, 2025, and [homelessness lecture](https://www.youtube.com/watch?v=0Emgzg0pry0),
October 21, 2026. It includes recordings on hypertension in older adults and
lipoprotein(a) from that archive.

[Mass General Brigham's asthma rounds](https://bwhedtech.media.partners.org/programs/asthma/)
provide pulmonary-function interpretation, asthma/metabolic disease and
telemedicine/home-monitoring teaching. [Banner's university grand rounds](https://library.bannerhealth.com/IMGR/20252026)
include asthma management, obesity/cardiovascular disease and colorectal cancer
screening. [Arizona's psychiatry archive](https://medicine.arizona.edu/psychiatry/grand-rounds-archive)
provides depression and cancer-related demoralization recordings. Availability of
the individual players and transcripts remains explicit in each source record.

Harvard's [healthcare curriculum](https://www.isc.hbs.edu/health-care/health-care-courses/Pages/health-care-curriculum.aspx),
[TDABC starter kit](https://www.isc.hbs.edu/Documents/pdf/2020_TDABC_Project_Starter_Kit.pdf)
and [value-based-care concepts](https://www.isc.hbs.edu/Documents/pdf/2019-Value-Based-Health-Care-Delivery-Core-Concepts-Porter.pdf)
support outcomes, care-cycle costs and coordinated care. The existing Porter
knowledge was retained; the candidate cards add explicit source-linked exercises
without changing the default corpus.

## What was actually read and used

The 18 background cards contain short attributed summaries. Open RN chapter text
was checked alongside its [Health Alterations license](https://wtcs.pressbooks.pub/healthalts/front-matter/preface/)
and [Health Promotion license](https://wtcs.pressbooks.pub/healthpromo/front-matter/preface/).
Embedded assets and assessments can have different licenses and were excluded.
NIDDK's [copyright page](https://www.niddk.nih.gov/copyright) was inspected for its
reuse conditions and exceptions. Public visibility alone was not labeled an open
commercial-training license. Full videos, transcript files, slides and publisher
answer sets were not ingested into model training.

The ACC's [HF in a Box](https://www.acc.org/Education-and-Meetings/Features/2025/05/HF-in-a-Box)
links a 73-slide PowerPoint. Selected slide text and references, especially slides
30-31 and 35-37, were inspected locally. The short candidate summary preserves the
distinction between treatment coverage, dose exposure, tolerance and dated
monitoring. The full deck, dosing instructions and trial effect sizes were not
adopted as a treatment protocol or gradient-training corpus. Its content hash is
`27ad8744c2af9b0a52de8bde05dff4e56e139b525cc2e082d736b3ae3472ccb7`.

Two concrete source-quality findings explain the distinction. An open COPD
chapter contains an oversimplified oxygen explanation, which was excluded from
the educational summary. Mayo's catalog labels one recording as a hypertension
update while the linked URL names pulmonary hypertension; that item was excluded
pending clarification. An academic source or an open license does not establish
that every teaching statement should become an executable rule.

## Connection to the existing learning loop

`tools/training/education_library.py` validates these explicit cards and builds a
candidate directory for the existing `vbc_knowledge.answer(..., directory=...)`
retriever. There is no additional retrieval service. Existing clinical-rule
cataloging returns zero operational rules for this library. Metadata cannot claim
full content review, physician approval, a transcript ingestion or a permissive
license. Provenance and availability remain attached to each lecture.

The broader analytics curriculum already practices source-bound distinctions
such as documented versus missing reasons, visits versus referrals, device orders
versus received observations, and outcomes versus financial scenarios. Its
reference answers come from independently checked synthetic source calculations,
not from copying a lecturer's answer or a model's prior output.

Further lecture reading can propose attributed facts, applicability requirements
and new clinical-style exercises. Any treatment rule needs its applicable dated
evidence. Supervision must retain its review status. The existing fit/calibration/
verification boundaries and immutable candidate comparison then apply. Source
reading by itself does not prove that a trained model improved.

Source manifests live in `config/agere/learning/academic-reading-cards.json` and
`academic-grand-rounds.json`. Local reading and retrieval receipts live under
`artifacts/agere-healthcare-learning-2026-10-27/research/`. The public PowerPoint
stays in local artifacts and is excluded from Git. No Claude or other provider
inference was used for this intake.
