import copy
import unittest
import yaml
from clinical_core.agere.clinical.planner_catalog import factor, serialize


def catalog():
    return {'question':'Do not alter commas, spaces | or : inside source data.',
      'cohort_fields':{'record_fields':{'notes':['source_system','text'],'labs':['source_system','value']}},
      'node_contracts':{'cohort':{'parameters':{'specification':{}},'meaning':'Empty all matches every patient.','inputs':[]},
                        'analyze':{'parameters':{'operator':'x'},'meaning':'Registered only.'}},
      'evidence_catalog':{'longitudinal':{'available':True,'streams':['adt','labs'],'event_types':['admission','adt','text_annotation']},
                          'concepts':{'text':['diagnosis ckd'],'structured':['financial_hardship','lab:a:b','lab:β']}}}


class CatalogTests(unittest.TestCase):
    def test_selector_empty_parameters_are_preserved_as_shared_fields(self):
        c=catalog()
        for v in c['node_contracts'].values():v['parameters']={}
        d=factor(c)['node_contracts'];self.assertEqual(d['shared'],{'parameters':{}})
        for row in d['rows']:
            decoded={**d['shared'],**dict(zip(d['columns'],row))}
            original=c['node_contracts'][decoded.pop('operation')]
            self.assertEqual(decoded,{**original,'inputs':original.get('inputs',['earlier_set'])})

    def test_factoring_preserves_all_field_and_concept_memberships(self):
        original=catalog();before=copy.deepcopy(original);d=factor(original)
        f=d['cohort_fields']['record_fields']
        for stream,fields in original['cohort_fields']['record_fields'].items():
            self.assertEqual(set(fields),set(f['common_to_all_streams'])|set(f['additional_by_stream'][stream]))
        l=d['evidence_catalog']['longitudinal'];v=l['event_types']
        added={prefix+suffix for prefix,values in l['streams']['plus_by_prefix'].items() for suffix in values}
        streams=(set(original['cohort_fields']['record_fields'])-set(l['streams']['cohort_streams_minus']))|added
        self.assertEqual(streams,set(original['evidence_catalog']['longitudinal']['streams']))
        self.assertEqual(set(original['evidence_catalog']['longitudinal']['event_types']),
                         (streams-set(v['streams_minus']))|set(v['plus']))
        g=d['evidence_catalog']['concepts']['structured_by_prefix']
        self.assertEqual(set(original['evidence_catalog']['concepts']['structured']),{prefix+s for prefix,rows in g.items() for s in rows})
        self.assertEqual(original,before)

    def test_serializer_preserves_punctuation_and_contract_semantics(self):
        original=catalog();raw=serialize(original).split('\n',1)[1];d=yaml.safe_load(raw)
        self.assertEqual(d,factor(original));self.assertEqual(d['question'],original['question'])
        columns=d['node_contracts']['columns'];rows=[dict(zip(columns,row)) for row in d['node_contracts']['rows']]
        self.assertEqual(rows[0]['inputs'],[]);self.assertEqual(rows[1]['inputs'],['earlier_set'])
        for row in rows:
            self.assertEqual(row['meaning'],original['node_contracts'][row['operation']]['meaning'])
            self.assertEqual(row['parameters'],original['node_contracts'][row['operation']]['parameters'])

    def test_absent_longitudinal_catalog_remains_unavailable(self):
        c=catalog();c['evidence_catalog']['longitudinal']={'available':False}
        self.assertEqual(factor(c)['evidence_catalog']['longitudinal'],{'available':False})

    def test_aliases_expand_to_identical_literals_and_contracts(self):
        c=catalog();phrase='unknown outside received source evidence, including punctuation: * &'
        c['repeated']=[{'description':phrase,'inputs':['earlier_population_set']} for _ in range(20)]
        raw=serialize(c).split('\n',1)[1]
        self.assertEqual(yaml.safe_load(raw),factor(c))
        self.assertLess(len(raw),len(yaml.safe_dump(factor(c),default_flow_style=True)))
        self.assertIn('&',raw)

    def test_identifier_groups_preserve_full_source_names(self):
        from clinical_core.agere.clinical.planner_catalog import identifier_groups
        values=['diagnostic_test_reports','diagnostic_test_sessions','diagnostic_orders','labs','long_singleton_name']
        grouped=identifier_groups(values)
        self.assertEqual(set(values),{prefix+suffix for prefix,rows in grouped.items() for suffix in rows})
        self.assertIn('diagnostic_',grouped)


if __name__=='__main__':unittest.main()
