"""Coverage of 200 real ClinicalTrials.gov eligibility lines (ctgov_criteria_sample.json, fetched 2026-10-23 from the public v2 API,
12 conditions, completed studies, seeded shuffle) by the note frames. Hand-assigned tags, one per line index:
frame tags C=condition Sy=symptom M=medication_event P=procedure_test V=finding_value A=adverse_event CP=care_process
SN=social_need FC=function_cognition PR=preference_refusal PF=plan_followup; ST=structured table (age, sex, site; answered by SQL,
not a note question); NC=not a chart question (consent, investigator judgment, study logistics, willingness); GAP=chart question the frames
cannot express. Run: python -m tools.note_labels.coverage_ctgov"""
import collections
import json
from pathlib import Path

SPEC = """0 V,1 C,2 V,3 NC,4 C,5 P,6 NC,7 V,8 V,9 CP,10 M,11 C,12 C,13 C,14 A,15 C,16 C,17 M,18 P,19 M,20 C,21 V,22 V,23 V,24 V,25 M,26 ST,27 V,28 V,29 NC,30 C,31 M,32 M,33 NC,34 PR,35 V,36 NC,37 C,38 C,39 NC,40 M,41 C,42 V,43 C,44 A,45 C,46 V,47 M,48 C,49 C,50 NC,51 ST,52 SN,53 SN,54 M,55 NC,56 CP,57 C,58 PF,59 NC,60 NC,61 SN,62 M,63 P,64 PF,65 V,66 A,67 V,68 C,69 C,70 PF,71 FC,72 P,73 NC,74 V,75 C,76 SN,77 SN,78 C,79 M,80 NC,81 V,82 ST,83 V,84 C,85 M,86 NC,87 M,88 NC,89 M,90 Sy,91 NC,92 P,93 C,94 M,95 FC,96 M,97 V,98 C,99 P,100 C,101 C,102 A,103 V,104 V,105 C,106 C,107 M,108 C,109 NC,110 ST,111 V,112 ST,113 V,114 NC,115 V,116 C,117 C,118 C,119 P,120 ST,121 V,122 V,123 SN,124 P,125 C,126 C,127 C,128 C,129 NC,130 C,131 V,132 C,133 C,134 P,135 P,136 ST,137 V,138 NC,139 PF,140 Sy,141 M,142 ST,143 C,144 C,145 C,146 NC,147 NC,148 NC,149 P,150 C,151 C,152 SN,153 C,154 CP,155 NC,156 C,157 ST,158 V,159 C,160 M,161 C,162 NC,163 A,164 V,165 ST,166 V,167 PF,168 C,169 NC,170 C,171 M,172 SN,173 P,174 C,175 V,176 V,177 M,178 M,179 V,180 ST,181 M,182 C,183 C,184 C,185 V,186 P,187 C,188 V,189 M,190 M,191 P,192 C,193 C,194 M,195 C,196 NC,197 V,198 C,199 P"""
FRAME = {'C': 'condition', 'Sy': 'symptom', 'M': 'medication_event', 'P': 'procedure_test', 'V': 'finding_value', 'A': 'adverse_event',
         'CP': 'care_process', 'SN': 'social_need', 'FC': 'function_cognition', 'PR': 'preference_refusal', 'PF': 'plan_followup'}


def tally():
    tags = {int(a): b for a, b in (x.split() for x in SPEC.split(','))}
    assert sorted(tags) == list(range(200)), set(range(200)) - set(tags)
    c = collections.Counter(tags.values())
    chart = 200 - c['NC']
    framed = sum(v for k, v in c.items() if k in FRAME)
    return {'lines': 200, 'not_a_chart_question': c['NC'], 'chart_questions': chart, 'structured_table': c['ST'],
            'expressible_by_frames': framed, 'gap': c['GAP'],
            'frames_share_of_chart_questions': round(framed / chart, 3),
            'frames_plus_structured_share': round((framed + c['ST']) / chart, 3),
            'by_frame': {FRAME[k]: v for k, v in c.items() if k in FRAME}}


if __name__ == '__main__':
    print(json.dumps(tally(), indent=1))
