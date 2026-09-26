"""
Where a set's runs go off this disk (web/storage.py): an S3 bucket or a
folder of a Drive chosen on the settings page, the token rclone authorize
prints, and a set's runs sent to a folder through rclone and back - with
rclone itself on a local folder, when it is on this machine.
"""

import io
import json
import os
import shutil
import tempfile
import types
import unittest
import urllib.error
from unittest import mock

from parity_deriva.lib import s3
from parity_deriva.tests.servers_test import SWEEP, saveSet, server
from parity_deriva.web import storage
from parity_deriva.web.service import ServiceError

TOKEN = {'access_token': 'ya29.a', 'token_type': 'Bearer', 'refresh_token': '1//r',
         'expiry': '2026-09-26T17:00:00Z'}
RCLONE = storage.rclone(types.SimpleNamespace())


class FakeRemote(object):
    """A Drive folder in memory; what `made` holds is every one built, with its rclone.conf then."""
    made = []

    def __init__(self, program, config, folder):
        with open(config) as handle:
            self.conf = handle.read()
        self.config, self.folder, self.held = config, folder, {}
        FakeRemote.made.append(self)

    def put(self, key, data):
        self.held[key] = data

    def get(self, key):
        return self.held[key]

    def delete(self, key):
        self.held.pop(key, None)


class TokenTest(unittest.TestCase):

    def test_the_command_is_the_one_rclone_config_prints(self):
        # rclone v1.75 config, for scope drive.file with and without a client of one's own
        self.assertEqual(storage.authorizeBlob('123-abc.apps.googleusercontent.com', 'XYZsecret'),
                         'eyJjbGllbnRfaWQiOiIxMjMtYWJjLmFwcHMuZ29vZ2xldXNlcmNvbnRlbnQuY29tIiwiY2xp'
                         'ZW50X3NlY3JldCI6IlhZWnNlY3JldCIsInNjb3BlIjoiZHJpdmUuZmlsZSJ9')
        self.assertEqual(storage.authorizeBlob(), 'eyJzY29wZSI6ImRyaXZlLmZpbGUifQ')

    def test_the_token_is_read_off_what_was_pasted(self):
        import base64
        pasted = ('Paste the following into your remote machine --->\n%s\n<---End paste\n'
                  % json.dumps(TOKEN))
        self.assertEqual(storage.token(pasted), TOKEN)
        self.assertEqual(storage.token(json.dumps(TOKEN)), TOKEN)
        blob = base64.urlsafe_b64encode(json.dumps({'token': json.dumps(TOKEN)}).encode()).decode()
        self.assertEqual(storage.token(blob.rstrip('=')), TOKEN)
        for wrong in ('', 'hello', json.dumps({'access_token': 'a'}), '[1, 2]'):
            with self.assertRaises(storage.StorageError):
                storage.token(wrong)


class ChoiceTest(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.folder)
        FakeRemote.made = []
        patch = mock.patch.object(storage, 'Remote', FakeRemote)
        patch.start()
        self.addCleanup(patch.stop)

    def conf(self):
        with open(storage.confPath(self.setup)) as handle:
            return handle.read()

    def test_nothing_chosen_is_env_s3_or_nothing(self):
        self.assertIsNone(storage.bucket(self.setup))
        self.assertEqual(storage.status(self.setup)['kind'], None)
        self.setup.__dict__.update(S3_ENDPOINT='https://s3.example', S3_BUCKET='b', S3_ACCESS_KEY='k',
                                   S3_SECRET_KEY='s')
        told = storage.status(self.setup)
        self.assertEqual((told['kind'], told['from'], told['s3']['bucket'], told['s3']['secretKey']),
                         ('s3', 'env', 'b', True))
        self.assertIsInstance(storage.bucket(self.setup), s3.Bucket)

    def test_a_google_drive_is_tested_then_kept_with_its_token_in_rclone_conf(self):
        with self.assertRaises(storage.StorageError):
            storage.save({'kind': 'gdrive', 'folder': 'parity'}, self.setup)
        told = storage.save({'kind': 'gdrive', 'folder': 'parity  runs', 'clientId': 'me.apps',
                             'clientSecret': 'shh', 'token': json.dumps(TOKEN)}, self.setup)
        self.assertEqual((told['kind'], told['from'], told['folder'], told['clientSecret'], told['token']),
                         ('gdrive', 'page', 'parity runs', True, True))
        conf = self.conf()
        for line in ('[cold]', 'type = drive', 'scope = drive.file', 'use_trash = false', 'client_id = me.apps',
                     'client_secret = shh', 'token = %s' % json.dumps(TOKEN)):
            self.assertIn(line, conf)
        self.assertEqual(oct(os.stat(storage.confPath(self.setup)).st_mode & 0o777), '0o600')
        self.assertEqual(oct(os.stat(storage.statePath(self.setup)).st_mode & 0o777), '0o600')
        # tested on a copy, the probe file gone after
        self.assertEqual((FakeRemote.made[-1].folder, FakeRemote.made[-1].held), ('parity runs', {}))
        where = storage.bucket(self.setup)
        self.assertEqual((where.folder, where.config), ('parity runs', storage.confPath(self.setup)))
        # another folder, the login kept
        storage.save({'kind': 'gdrive', 'folder': 'other', 'clientId': 'me.apps'}, self.setup)
        self.assertEqual((storage.kept(self.setup)['folder'], storage.kept(self.setup)['clientSecret']), ('other', 'shh'))
        self.assertIn('token = %s' % json.dumps(TOKEN), self.conf())
        for wrong in ('../up', '', '/'):
            with self.assertRaises(storage.StorageError):
                storage.save({'kind': 'gdrive', 'folder': wrong or '...', 'token': json.dumps(TOKEN)}, self.setup)
        storage.save({'kind': 'none'}, self.setup)
        self.assertFalse(os.path.exists(storage.confPath(self.setup)))
        self.assertIsNone(storage.bucket(self.setup))

    def test_a_failed_test_keeps_what_there_was(self):
        storage.save({'kind': 'gdrive', 'folder': 'parity', 'token': json.dumps(TOKEN)}, self.setup)
        before = self.conf()

        class Broken(FakeRemote):
            def put(self, key, data):
                raise s3.S3Error("rclone copyto: couldn't find root directory ID: 403 insufficientPermissions")
        with mock.patch.object(storage, 'Remote', Broken), self.assertRaises(storage.StorageError) as caught:
            storage.save({'kind': 'gdrive', 'folder': 'new', 'token': json.dumps(dict(TOKEN, access_token='b'))},
                         self.setup)
        self.assertIn('insufficientPermissions', str(caught.exception))
        self.assertEqual((self.conf(), storage.kept(self.setup)['folder']), (before, 'parity'))
        self.assertFalse(os.path.exists(storage.confPath(self.setup) + '.trial'))

    def test_a_onedrive_names_its_drive(self):
        with mock.patch.object(storage, 'oneDrive', lambda held: ('b!xyz', 'personal')):
            storage.save({'kind': 'onedrive', 'folder': 'parity', 'token': json.dumps(TOKEN)}, self.setup)
        conf = self.conf()
        for line in ('type = onedrive', 'drive_id = b!xyz', 'drive_type = personal'):
            self.assertIn(line, conf)
        self.assertNotIn('scope', conf)

        def expired(request, timeout=None):
            self.assertEqual(request.get_header('Authorization'), 'Bearer ya29.a')
            raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b''))
        with mock.patch('urllib.request.urlopen', expired), self.assertRaises(storage.StorageError) as caught:
            storage.oneDrive(TOKEN)
        self.assertIn('valid for an hour', str(caught.exception))

    def test_an_s3_bucket_on_the_page_keeps_its_secret(self):
        made = []

        class Bucket(FakeRemote):
            def __init__(self, endpoint, bucket, access, secret, region='', prefix=''):
                self.args, self.held = (endpoint, bucket, access, secret, region, prefix), {}
                made.append(self)
        fields = {'kind': 's3', 'endpoint': 'https://s3.example', 'bucket': 'b', 'accessKey': 'k',
                  'secretKey': 's', 'prefix': 'p'}
        with mock.patch.object(storage.s3, 'Bucket', Bucket):
            with self.assertRaises(storage.StorageError):
                storage.save(dict(fields, secretKey=''), self.setup)
            told = storage.save(fields, self.setup)
            self.assertEqual((told['from'], told['s3']['secretKey'], told['s3']['bucket']), ('page', True, 'b'))
            storage.save(dict(fields, secretKey='', bucket='c'), self.setup)
        self.assertEqual(made[-1].args, ('https://s3.example', 'c', 'k', 's', 'us-east-1', 'p'))


@unittest.skipUnless(RCLONE, 'rclone is not on this machine')
class RcloneTest(unittest.TestCase):
    """rclone itself, on a local folder standing for the Drive."""

    def setUp(self):
        self.service = server(self)
        self.drive = tempfile.mkdtemp(prefix='parity-deriva-drive-')
        self.addCleanup(shutil.rmtree, self.drive, True)
        data = self.service.setup.DATA_DIR
        with open(os.path.join(data, 'rclone.conf'), 'w') as handle:
            handle.write('[cold]\ntype = local\n')
        with open(os.path.join(data, 'storage.json'), 'w') as handle:
            json.dump({'kind': 'gdrive', 'folder': self.drive}, handle)

    def test_the_calls_of_a_bucket(self):
        where = storage.bucket(self.service.setup)
        storage.probe(where)
        where.put('a/b.json.gz', b'\x1f\x8b binary')
        self.assertEqual(where.get('a/b.json.gz'), b'\x1f\x8b binary')
        with self.assertRaises(s3.S3Error) as caught:
            where.get('a/missing.json.gz')
        self.assertEqual(caught.exception.status, 404)
        where.delete('a/b.json.gz')
        where.delete('a/b.json.gz')
        self.assertEqual(os.listdir(os.path.join(self.drive, 'a')), [])

    def test_a_sets_runs_go_to_the_folder_and_come_back(self):
        saveSet(self.service)
        folder = self.service.sweepPath(SWEEP, '')
        with open(os.path.join(folder, '1.json.gz.part'), 'wb') as handle:
            handle.write(b'half')
        told = self.service.freeze(SWEEP)
        self.assertEqual(told['files'], 2)
        self.assertEqual(sorted(os.listdir(os.path.join(self.drive, 'sweeps', SWEEP))), ['1.json.gz', '2.json.gz'])
        self.assertEqual(os.listdir(folder), ['1.json.gz.part'])
        self.assertEqual(self.service.sweepPayload(SWEEP, 2)['trades'][0]['pl'], 5.0)
        self.service.deleteSweep(SWEEP)
        self.assertFalse(os.path.exists(os.path.join(self.drive, 'sweeps', SWEEP)))
        self.assertEqual(self.service.sweeps(), [])

    def test_a_missing_rclone_is_said(self):
        where = storage.Remote(None, 'x.conf', 'f')
        with self.assertRaises(s3.S3Error) as caught:
            where.get('k')
        self.assertIn('rclone is not on this server', str(caught.exception))
        with mock.patch.object(storage, 'rclone', lambda setup: None), self.assertRaises(ServiceError):
            saveSet(self.service)
            self.service.freeze(SWEEP)


if __name__ == '__main__':
    unittest.main()
