import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from clinical_core.agere.hosted.secrets import CredentialStore, SecretBox, SecretError
from clinical_core.agere.onboarding import connectors as C

SECRET = 'sekret-pw-9f3a'
SF = {'SNOWFLAKE_ACCOUNT': 'acct-x', 'SNOWFLAKE_USER': 'svc_agere', 'SNOWFLAKE_WAREHOUSE': 'WH', 'SNOWFLAKE_PASSWORD': SECRET,
      'SNOWFLAKE_ROLE': 'READER'}


class SecretBoxTests(unittest.TestCase):
    def test_round_trip(self):
        box = SecretBox(SecretBox.generate_key())
        tok = box.encrypt({'a': SECRET, 'n': 3})
        self.assertNotIn(SECRET, tok)
        self.assertEqual(box.decrypt(tok), {'a': SECRET, 'n': 3})

    def test_wrong_key_and_corrupt_token_fail_cleanly(self):
        tok = SecretBox(SecretBox.generate_key()).encrypt({'p': SECRET})
        other = SecretBox(SecretBox.generate_key())
        for bad in (tok, tok[:-4] + 'AAAA', 'garbage', ''):
            with self.assertRaises(SecretError) as cm: other.decrypt(bad)
            self.assertNotIn(SECRET, str(cm.exception))
            self.assertIsNone(cm.exception.__cause__)

    def test_invalid_key(self):
        for bad in ('', 'short', 'x' * 44, b'\x00' * 32, None):
            with self.assertRaises(SecretError) as cm: SecretBox(bad)
            if isinstance(bad, str) and bad: self.assertNotIn(bad, str(cm.exception))
        key = SecretBox.generate_key()
        self.assertNotIn(key, repr(SecretBox(key)))
        with self.assertRaisesRegex(SecretError, 'AGERE_SECRET_KEY'): SecretBox.from_env({})
        self.assertIsInstance(SecretBox.from_env({'AGERE_SECRET_KEY': key}), SecretBox)

    def test_non_serializable_values(self):
        with self.assertRaises(SecretError): SecretBox(SecretBox.generate_key()).encrypt({'k': object()})
        with self.assertRaises(SecretError): SecretBox(SecretBox.generate_key()).encrypt(['not', 'a', 'dict'])


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'auth' / 'auth.sqlite'
        self.box = SecretBox(SecretBox.generate_key())
        self.store = CredentialStore(self.path, self.box)

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def test_put_get_update_delete(self):
        self.store.put('org1', 'warehouse', SF, kind='snowflake')
        self.assertEqual(self.store.get('org1', 'warehouse'), SF)
        first = sqlite3.connect(self.path).execute('SELECT created_at FROM credentials').fetchone()[0]
        self.store.put('org1', 'warehouse', {**SF, 'SNOWFLAKE_PASSWORD': 'new'}, kind='snowflake')
        self.assertEqual(self.store.get('org1', 'warehouse')['SNOWFLAKE_PASSWORD'], 'new')
        row = sqlite3.connect(self.path).execute('SELECT created_at, count(*) FROM credentials').fetchone()
        self.assertEqual(row, (first, 1))
        self.assertTrue(self.store.delete('org1', 'warehouse'))
        self.assertFalse(self.store.delete('org1', 'warehouse'))
        with self.assertRaises(KeyError): self.store.get('org1', 'warehouse')

    def test_ciphertext_at_rest_and_wal(self):
        self.store.put('org1', 'w', SF)
        raw = self.path.read_bytes() + (self.path.with_name(self.path.name + '-wal').read_bytes()
                                        if self.path.with_name(self.path.name + '-wal').exists() else b'')
        self.assertNotIn(SECRET.encode(), raw)
        mode = sqlite3.connect(self.path).execute('PRAGMA journal_mode').fetchone()[0]
        self.assertEqual(mode, 'wal')

    def test_names_hides_values(self):
        self.store.put('org1', 'b', SF, kind='snowflake')
        self.store.put('org1', 'a', {'DATABRICKS_TOKEN': SECRET})
        names = self.store.names('org1')
        self.assertEqual([n['name'] for n in names], ['a', 'b'])
        self.assertEqual(set(names[0]), {'name', 'kind', 'updated_at'})
        self.assertEqual(names[1]['kind'], 'snowflake')
        self.assertNotIn(SECRET, json.dumps(names))

    def test_isolation_by_org(self):
        self.store.put('org1', 'w', SF)
        self.store.put('org2', 'w', {'SNOWFLAKE_PASSWORD': 'other'})
        self.assertEqual(self.store.get('org2', 'w'), {'SNOWFLAKE_PASSWORD': 'other'})
        self.assertEqual(self.store.names('org3'), [])
        with self.assertRaises(KeyError): self.store.get('org3', 'w')
        self.store.delete('org2', 'w')
        self.assertEqual(self.store.get('org1', 'w'), SF)
        # A ciphertext copied into another tenant's slot does not open there.
        db = sqlite3.connect(self.path)
        ct = db.execute("SELECT ciphertext FROM credentials WHERE org='org1'").fetchone()[0]
        db.execute("INSERT INTO credentials VALUES ('org9','w',?,NULL,'t','t')", (ct,)); db.commit()
        with self.assertRaises(SecretError) as cm: self.store.get('org9', 'w')
        self.assertNotIn(SECRET, str(cm.exception))

    def test_wrong_key_store(self):
        self.store.put('org1', 'w', SF)
        other = CredentialStore(self.path, SecretBox(SecretBox.generate_key()))
        with self.assertRaises(SecretError) as cm: other.get('org1', 'w')
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertEqual(other.names('org1')[0]['name'], 'w')
        other.close()

    def test_rotation(self):
        self.store.put('org1', 'w', SF); self.store.put('org2', 'x', {'k': 'v'})
        new = SecretBox(SecretBox.generate_key())
        self.assertEqual(self.store.rotate(self.box, new), 2)
        self.assertEqual(self.store.get('org1', 'w'), SF)
        fresh_old = CredentialStore(self.path, self.box)
        with self.assertRaises(SecretError): fresh_old.get('org1', 'w')
        self.assertEqual(CredentialStore(self.path, new).get('org2', 'x'), {'k': 'v'})
        self.assertEqual(self.store.rotate(self.box, new), 0)  # resumed rotation is a no-op
        fresh_old.close()

    def test_rotation_aborts_atomically(self):
        self.store.put('org1', 'w', SF)
        stranger = SecretBox(SecretBox.generate_key())
        CredentialStore(self.path, stranger).put('org2', 'x', {'k': 'v'})
        before = sqlite3.connect(self.path).execute('SELECT ciphertext FROM credentials ORDER BY org').fetchall()
        new = SecretBox(SecretBox.generate_key())
        with self.assertRaises(SecretError): self.store.rotate(self.box, new)
        after = sqlite3.connect(self.path).execute('SELECT ciphertext FROM credentials ORDER BY org').fetchall()
        self.assertEqual(before, after)
        self.assertEqual(self.store.get('org1', 'w'), SF)

    def test_threads(self):
        errors = []
        def work(i):
            try:
                for j in range(20):
                    self.store.put(f'org{i}', f'n{j}', {'v': f'{i}-{j}'})
                    assert self.store.get(f'org{i}', f'n{j}') == {'v': f'{i}-{j}'}
                self.store.close()
            except Exception as e: errors.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.names('org3')), 20)

    def test_bad_labels(self):
        for org, name in (('', 'w'), ('o', ''), ('o\n', 'w'), (None, 'w')):
            with self.assertRaises(ValueError): self.store.put(org, name, {'k': 'v'})


def _sentinel_env():
    return {'SNOWFLAKE_ACCOUNT': 'ENV-ACCT', 'SNOWFLAKE_USER': 'ENV-USER', 'SNOWFLAKE_WAREHOUSE': 'ENV-WH',
            'SNOWFLAKE_PASSWORD': 'ENV-PW', 'DATABRICKS_TOKEN': 'ENV-TOKEN', 'DATABRICKS_SERVER_HOSTNAME': 'env-host',
            'DATABRICKS_HTTP_PATH': '/env', 'PGPASSWORD': 'ENV-PG'}


class ConnectorCredentialTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, _sentinel_env(), clear=False)
        p.start(); self.addCleanup(p.stop)
        self.env_before = dict(os.environ)

    def assertEnvUntouched(self):
        self.assertEqual(dict(os.environ), self.env_before)

    def test_snowflake_password_from_mapping(self):
        sf = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'snowflake': sf, 'snowflake.connector': sf.connector}):
            conn = C.connect('snowflake://DB/SCH', credentials=SF)
        kw = sf.connector.connect.call_args.kwargs
        self.assertEqual((kw['account'], kw['user'], kw['warehouse'], kw['password'], kw['role'], kw['database']),
                         ('acct-x', 'svc_agere', 'WH', SECRET, 'READER', 'DB'))
        self.assertNotIn(SECRET, json.dumps(conn.describe()))
        self.assertEnvUntouched()

    def test_snowflake_private_key_pem(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(b'pp')).decode()
        creds = {k: v for k, v in SF.items() if k != 'SNOWFLAKE_PASSWORD'}
        creds.update(SNOWFLAKE_PRIVATE_KEY=pem, SNOWFLAKE_PRIVATE_KEY_PASSPHRASE='pp')
        sf = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'snowflake': sf, 'snowflake.connector': sf.connector}):
            C.connect('snowflake://DB/SCH', credentials=creds)
            kw = sf.connector.connect.call_args.kwargs
            self.assertNotIn('password', kw)
            self.assertEqual(serialization.load_der_private_key(kw['private_key'], None).private_numbers(), key.private_numbers())
            creds['SNOWFLAKE_PRIVATE_KEY_PASSPHRASE'] = 'wrong-pass-123'
            with self.assertRaises(RuntimeError) as cm: C.connect('snowflake://DB/SCH', credentials=creds)
        self.assertNotIn('wrong-pass-123', str(cm.exception)); self.assertNotIn('PRIVATE KEY-----', str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)
        self.assertEnvUntouched()

    def test_snowflake_mapping_ignores_environment(self):
        sf = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'snowflake': sf, 'snowflake.connector': sf.connector}):
            with self.assertRaises(RuntimeError) as cm:
                C.connect('snowflake://DB/SCH', credentials={'SNOWFLAKE_ACCOUNT': 'a', 'SNOWFLAKE_PASSWORD': SECRET})
        msg = str(cm.exception)
        self.assertIn('SNOWFLAKE_USER', msg); self.assertIn('SNOWFLAKE_WAREHOUSE', msg)
        self.assertNotIn(SECRET, msg); self.assertNotIn('ENV-', msg)
        sf.connector.connect.assert_not_called()
        self.assertEnvUntouched()

    def test_databricks_from_mapping(self):
        db = mock.MagicMock()
        creds = {'DATABRICKS_SERVER_HOSTNAME': 'h.cloud', 'DATABRICKS_HTTP_PATH': '/sql/1', 'DATABRICKS_TOKEN': SECRET}
        with mock.patch.dict(sys.modules, {'databricks': db, 'databricks.sql': db.sql}):
            conn = C.connect('databricks://cat/sch', credentials=creds)
            kw = db.sql.connect.call_args.kwargs
            self.assertEqual((kw['server_hostname'], kw['http_path'], kw['access_token'], kw['catalog']), ('h.cloud', '/sql/1', SECRET, 'cat'))
            self.assertNotIn(SECRET, json.dumps(conn.describe()))
            with self.assertRaises(RuntimeError) as cm:
                C.connect('databricks://cat/sch', credentials={'DATABRICKS_TOKEN': SECRET})
        self.assertIn('DATABRICKS_SERVER_HOSTNAME', str(cm.exception)); self.assertNotIn(SECRET, str(cm.exception))
        self.assertEnvUntouched()

    def _bq_modules(self):
        bq, dbapi, sa = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        google, cloud, oauth2 = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        cloud.bigquery, bq.dbapi, google.cloud, google.oauth2, oauth2.service_account = bq, dbapi, cloud, oauth2, sa
        mods = {'google': google, 'google.cloud': cloud, 'google.cloud.bigquery': bq, 'google.cloud.bigquery.dbapi': dbapi,
                'google.oauth2': oauth2, 'google.oauth2.service_account': sa}
        return mods, bq, dbapi, sa

    def test_bigquery_service_account_json(self):
        mods, bq, dbapi, sa = self._bq_modules()
        info = {'type': 'service_account', 'private_key': SECRET, 'client_email': 'x@p.iam'}
        with mock.patch.dict(sys.modules, mods):
            C.connect('bigquery://proj/ds', credentials={'GOOGLE_APPLICATION_CREDENTIALS_JSON': json.dumps(info)})
            sa.Credentials.from_service_account_info.assert_called_once_with(info)
            self.assertIs(bq.Client.call_args.kwargs['credentials'], sa.Credentials.from_service_account_info.return_value)
            self.assertEqual(bq.Client.call_args.kwargs['project'], 'proj')
            C.connect('bigquery://proj/ds', credentials={'GOOGLE_APPLICATION_CREDENTIALS': '/keys/sa.json'})
            sa.Credentials.from_service_account_file.assert_called_once_with('/keys/sa.json')
            with self.assertRaises(RuntimeError) as cm:
                C.connect('bigquery://proj/ds', credentials={'GOOGLE_APPLICATION_CREDENTIALS_JSON': '{"private_key": "' + SECRET})
            self.assertNotIn(SECRET, str(cm.exception)); self.assertIsNone(cm.exception.__cause__)
            with self.assertRaisesRegex(RuntimeError, 'GOOGLE_APPLICATION_CREDENTIALS_JSON'):
                C.connect('bigquery://proj/ds', credentials={})
            # Without credentials: unchanged ADC path, no credentials kwarg.
            bq.Client.reset_mock()
            C.connect('bigquery://proj/ds')
            self.assertEqual(bq.Client.call_args.kwargs, {'project': 'proj'})
        self.assertEnvUntouched()

    def test_postgres_adds_mapping_to_dsn(self):
        pg = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'psycopg': pg}):
            conn = C.connect('postgresql://db.internal/clinic', credentials={'PGUSER': 'reader', 'PGPASSWORD': SECRET, 'PGPORT': 6543})
            args, kw = pg.connect.call_args
            self.assertEqual(args, ('postgresql://db.internal/clinic',))
            self.assertEqual(kw, {'autocommit': True, 'user': 'reader', 'password': SECRET, 'port': '6543'})
            self.assertNotIn(SECRET, json.dumps(conn.describe()))
            C.connect('postgresql://db.internal/clinic')
            self.assertEqual(pg.connect.call_args.kwargs, {'autocommit': True})
        self.assertEnvUntouched()

    def test_without_credentials_env_behaviour_unchanged(self):
        sf = mock.MagicMock()
        with mock.patch.dict(sys.modules, {'snowflake': sf, 'snowflake.connector': sf.connector}):
            C.connect('snowflake://DB/SCH')
        self.assertEqual(sf.connector.connect.call_args.kwargs['password'], 'ENV-PW')

    def test_credentials_must_be_mapping(self):
        with self.assertRaises(TypeError): C.connect('snowflake://DB/SCH', credentials='SNOWFLAKE_PASSWORD=x')

    def test_store_to_connector(self):
        with tempfile.TemporaryDirectory() as d:
            store = CredentialStore(Path(d) / 'a.sqlite', SecretBox(SecretBox.generate_key()))
            store.put('org1', 'wh', SF, kind='snowflake')
            sf = mock.MagicMock()
            with mock.patch.dict(sys.modules, {'snowflake': sf, 'snowflake.connector': sf.connector}):
                C.connect('snowflake://DB/SCH', credentials=store.get('org1', 'wh'))
            self.assertEqual(sf.connector.connect.call_args.kwargs['password'], SECRET)
            store.close()
        self.assertEnvUntouched()


if __name__ == '__main__':
    unittest.main()
