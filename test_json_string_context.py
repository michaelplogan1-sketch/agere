"""Canonical serialized cells retain exact bytes under optional wire sharing."""
import copy,json,unittest
from clinical_core.agere.deliverables.receipts import compact_json,uncompact_json,unshare_json,share_json

class SerializedCells(unittest.TestCase):
    def test_nested_strings_marker_literals_unicode_and_numeric_types_round_trip(self):
        item={'literal':{'$':3},'escape':{'$literal':[['x',1]]},'number':1.0,'text':'Café "source"\nnext'}
        forms=[json.dumps(item,ensure_ascii=ascii,separators=separators)
               for ascii in (True,False)for separators in ((',',':'),(', ',': '))]
        value={'rows':[{'id':str(i),'serialized':s,'nested':json.dumps({'original':s}),'native':item}for i,s in enumerate(forms)],
            'noncanonical':'[ 1 , 2 ]','ordinary':'{not json}','nonfinite':'[NaN]'}
        encoded=compact_json(value,expand_json_strings=True)
        self.assertEqual(uncompact_json(encoded),value)
        self.assertEqual(uncompact_json(compact_json(value)),value)
        self.assertEqual(uncompact_json(encoded)['rows'][0]['native']['number'],1.0)

    def test_modified_or_missing_string_layout_fails_fingerprint_validation(self):
        encoded=compact_json({'cell':json.dumps({'a':[1,2]})},expand_json_strings=True)
        body=unshare_json(encoded['shared']);self.assertTrue(body['json_strings'])
        bad=copy.deepcopy(encoded);body['json_strings'][0]['at']=['missing'];bad['shared']=share_json(body)
        with self.assertRaises(ValueError):uncompact_json(bad)
        body=unshare_json(encoded['shared']);body['json_strings'][0]['format']='unsupported';bad['shared']=share_json(body)
        with self.assertRaises(ValueError):uncompact_json(bad)

if __name__=='__main__':unittest.main()
