"""
Where a set's runs go off this disk (web/service.py freeze): an S3 bucket,
a folder of the user's Google Drive, or of their OneDrive - chosen on the
settings page, or the S3 bucket PARITY_DERIVA_S3_* in .env names.

The page's choice is DATA_DIR/storage.json, readable by the service's user
only (an S3 secret may be in it). A Drive is reached through rclone, which
does the logins, the uploads and the checks on each: the user runs
`rclone authorize` on a PC with a browser, the command the page gives, and
pastes what it prints on the page. That token goes into DATA_DIR/rclone.conf,
where rclone keeps it fresh from then on. A choice is kept only once a file
was written there, read back and deleted.
"""

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from parity_deriva.lib import s3

DRIVES = ('gdrive', 'onedrive')
#: the rclone backend of each
BACKEND = {'gdrive': 'drive', 'onedrive': 'onedrive'}
#: seconds an rclone call may take: a set's runs are some hundreds of MB
TIMEOUT = 1800


class StorageError(Exception):
	"""A choice of storage refused, in words for the page."""


def dataDir(setup):
	return getattr(setup, 'DATA_DIR', '') or '.'


def statePath(setup):
	return os.path.join(dataDir(setup), 'storage.json')


def confPath(setup):
	return os.path.join(dataDir(setup), 'rclone.conf')


def _private(path, text):
	part = path + '.part'
	with open(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
		handle.write(text)
	os.replace(part, path)


def kept(setup):
	"""What the page chose, {} for nothing: then .env's bucket, if it names one."""
	try:
		with open(statePath(setup)) as handle:
			found = json.load(handle)
	except (OSError, ValueError):
		return {}
	return found if isinstance(found, dict) and found.get('kind') in ('s3',) + DRIVES else {}


def rclone(setup):
	"""rclone's path: PARITY_DERIVA_RCLONE, else beside this Python (a venv's bin), else on the PATH."""
	for found in (getattr(setup, 'RCLONE', None), os.path.join(os.path.dirname(sys.executable), 'rclone'),
				  shutil.which('rclone')):
		if found and os.access(found, os.X_OK):
			return found
	return None


class Remote(object):
	"""
	A folder of a Drive through rclone, with the calls lib/s3.Bucket has -
	and two of its own, a whole folder sent or dropped in one call.
	"""

	def __init__(self, program, config, folder):
		self.program, self.config, self.folder = program, config, folder

	def run(self, *args):
		if not self.program:
			raise s3.S3Error("rclone is not on this server: put it beside the service's Python "
							 "(the venv's bin), on the PATH, or set PARITY_DERIVA_RCLONE")
		try:
			done = subprocess.run([self.program, '--config', self.config, '-q'] + list(args),
								  capture_output=True, timeout=TIMEOUT)
		except (OSError, subprocess.TimeoutExpired) as exc:
			raise s3.S3Error("rclone: %s" % exc)
		said = done.stderr.decode('utf-8', 'replace').strip().splitlines()
		# 3 and 4: no such directory, no such file
		if done.returncode in (3, 4):
			raise s3.S3Error("not there: %s" % (said[-1] if said else args[-1]), status=404)
		if done.returncode:
			raise s3.S3Error("rclone %s: %s" % (args[0], said[-1] if said else 'exit %d' % done.returncode))
		return done.stdout

	def path(self, key):
		return 'cold:%s/%s' % (self.folder, key)

	def put(self, key, data):
		# from a file, so that rclone checks what arrived against it
		with tempfile.NamedTemporaryFile(dir=os.path.dirname(self.config) or '.', delete=False) as handle:
			handle.write(data)
		try:
			self.run('copyto', handle.name, self.path(key))
		finally:
			os.remove(handle.name)

	def get(self, key):
		return self.run('cat', self.path(key))

	def delete(self, key):
		try:
			self.run('deletefile', self.path(key))
		except s3.S3Error as exc:
			if exc.status != 404:
				raise

	def list(self, prefix=''):
		"""Every file under `prefix`, [{'key', 'size', 'time'}], as lib/s3.Bucket lists them."""
		try:
			found = json.loads(self.run('lsjson', '-R', '--files-only', self.path(prefix)) or b'[]')
		except s3.S3Error as exc:
			if exc.status == 404:
				return []
			raise
		return [{'key': prefix + f['Path'], 'size': f['Size'], 'time': f['ModTime']} for f in found]

	def sendAll(self, folder, prefix):
		"""Every file of `folder` under `prefix`, several at once, each checked by rclone."""
		self.run('copy', '--exclude', '*.part', folder, self.path(prefix))

	def dropAll(self, prefix):
		try:
			self.run('purge', self.path(prefix))
		except s3.S3Error as exc:
			if exc.status != 404:
				raise


def bucket(setup):
	"""Where the runs go: the page's choice, else .env's S3 bucket, else None."""
	found = kept(setup)
	if found.get('kind') == 's3':
		b = found['s3']
		return s3.Bucket(b['endpoint'], b['bucket'], b['accessKey'], b['secretKey'],
						 region=b.get('region') or 'us-east-1', prefix=b.get('prefix') or '')
	if found.get('kind') in DRIVES:
		return Remote(rclone(setup), confPath(setup), found['folder'])
	return s3.fromSettings(setup)


def status(setup):
	"""The settings page's box: what is chosen and from where, secrets said to be there, not shown."""
	found = kept(setup)
	out = {'kind': found.get('kind'), 'from': 'page' if found else None, 'rclone': rclone(setup),
		   'folder': found.get('folder') or 'parity-deriva', 'clientId': found.get('clientId') or '',
		   'clientSecret': bool(found.get('clientSecret')), 'token': found.get('kind') in DRIVES,
		   's3': dict(found.get('s3') or {}, secretKey=bool((found.get('s3') or {}).get('secretKey')))}
	if not found and s3.fromSettings(setup) is not None:
		out.update(kind='s3', **{'from': 'env'})
		out['s3'] = {'endpoint': setup.S3_ENDPOINT, 'bucket': setup.S3_BUCKET, 'accessKey': setup.S3_ACCESS_KEY,
					 'region': getattr(setup, 'S3_REGION', None), 'prefix': getattr(setup, 'S3_PREFIX', None),
					 'secretKey': True}
	return out


def authorizeBlob(clientId='', clientSecret=''):
	"""
	The argument of `rclone authorize "drive" <blob>` that asks for access to
	the files rclone makes and nothing else (drive.file), with the user's own
	client when given: base64 of the options, as rclone config prints it.
	"""
	options = dict([('client_id', clientId), ('client_secret', clientSecret)] if clientId else [])
	options['scope'] = 'drive.file'
	return base64.urlsafe_b64encode(json.dumps(options, separators=(',', ':')).encode()).rstrip(b'=').decode()


def token(text):
	"""
	The token out of what `rclone authorize` printed, pasted whole or not:
	its JSON between the arrows, or a base64 blob of it.
	"""
	text = str(text or '')
	if '--->' in text:
		text = text.split('--->', 1)[1].split('<---', 1)[0]
	text = text.strip()
	found = None
	try:
		found = json.loads(text)
	except ValueError:
		try:
			found = json.loads(base64.urlsafe_b64decode(text + '=' * (-len(text) % 4)))
		except (ValueError, TypeError):
			pass
	if isinstance(found, dict) and isinstance(found.get('token'), str):
		try:
			found = json.loads(found['token'])
		except ValueError:
			found = None
	if not isinstance(found, dict) or not found.get('access_token') or not found.get('refresh_token'):
		raise StorageError("paste what rclone authorize printed: the line between ---> and <---End paste")
	return found


def oneDrive(held):
	"""The id and the kind of the OneDrive a token opens, which rclone's configuration names."""
	request = urllib.request.Request('https://graph.microsoft.com/v1.0/me/drive',
									 headers={'Authorization': 'Bearer %s' % held['access_token']})
	try:
		with urllib.request.urlopen(request, timeout=30) as answer:
			found = json.load(answer)
	except urllib.error.HTTPError as exc:
		if exc.code == 401:
			raise StorageError("Microsoft refused the token - it is valid for an hour: run rclone "
							   "authorize again and paste what it prints")
		raise StorageError("Microsoft said %s to the token" % exc.code)
	except (OSError, ValueError) as exc:
		raise StorageError("Microsoft cannot be reached: %s" % exc)
	return found['id'], found['driveType']


def probe(where):
	"""A file written, read back and deleted: a StorageError saying what failed, else nothing."""
	key, body = 'probe-%s.txt' % secrets.token_hex(4), b'parity-deriva wrote this and deletes it'
	try:
		where.put(key, body)
		if where.get(key) != body:
			raise StorageError("the file read back is not the one written")
		where.delete(key)
	except s3.S3Error as exc:
		if 'invalid_grant' in str(exc):
			raise StorageError("the login was refused (invalid_grant): run the command on the PC "
							   "again and paste what it prints now")
		raise StorageError("the test write failed: %s" % exc)


def save(asked, setup):
	"""
	The page's choice: {"kind": "none"} forgets it, "s3" with the bucket's
	fields, "gdrive" or "onedrive" with the folder and what rclone authorize
	printed - a secret or a token left out is the one kept. Tested first
	(probe); nothing changes when the test fails.
	"""
	kind = asked.get('kind')
	before = kept(setup)
	if kind == 'none':
		for path in (statePath(setup), confPath(setup)):
			if os.path.exists(path):
				os.remove(path)
		return status(setup)
	if kind == 's3':
		held = (before.get('s3') or {}) if before.get('kind') == 's3' else {}
		fields = dict((k, str(asked.get(k) or '').strip()) for k in ('endpoint', 'bucket', 'accessKey',
																	 'secretKey', 'region', 'prefix'))
		fields['secretKey'] = fields['secretKey'] or held.get('secretKey') or ''
		if not fields['endpoint'].startswith(('https://', 'http://')) or not all(
				fields[k] for k in ('bucket', 'accessKey', 'secretKey')):
			raise StorageError("an S3 bucket has its endpoint (https://...), its name, an access key and a secret")
		probe(s3.Bucket(fields['endpoint'], fields['bucket'], fields['accessKey'], fields['secretKey'],
						region=fields['region'] or 'us-east-1', prefix=fields['prefix']))
		_private(statePath(setup), json.dumps({'kind': 's3', 's3': fields}))
		return status(setup)
	if kind not in DRIVES:
		raise StorageError("the storage is none, s3, gdrive or onedrive")
	folder = ' '.join(str(asked.get('folder') or 'parity-deriva').split())
	if not re.fullmatch(r'[\w .-]{1,80}', folder) or folder.strip('.') == '':
		raise StorageError("the folder: letters, digits, spaces, dots and dashes")
	same = before.get('kind') == kind
	clientId = str(asked.get('clientId') or '').strip()
	clientSecret = str(asked.get('clientSecret') or '').strip() or (
		before.get('clientSecret') if same and before.get('clientId') == clientId else '')
	if str(asked.get('token') or '').strip():
		held = token(asked['token'])
		lines = ['[cold]', 'type = %s' % BACKEND[kind], 'token = %s' % json.dumps(held)]
		if kind == 'gdrive':
			lines += ['scope = drive.file', 'use_trash = false']
			if clientId:
				lines += ['client_id = %s' % clientId, 'client_secret = %s' % clientSecret]
		else:
			drive, driveType = oneDrive(held)
			lines += ['drive_id = %s' % drive, 'drive_type = %s' % driveType]
		trial = confPath(setup) + '.trial'
		_private(trial, '\n'.join(lines) + '\n')
	elif same and os.path.exists(confPath(setup)):
		# the folder changed, the login kept: a copy to test with
		trial = confPath(setup) + '.trial'
		with open(confPath(setup)) as handle:
			_private(trial, handle.read())
	else:
		raise StorageError("paste what rclone authorize printed")
	try:
		probe(Remote(rclone(setup), trial, folder))
		os.replace(trial, confPath(setup))
	finally:
		if os.path.exists(trial):
			os.remove(trial)
	_private(statePath(setup), json.dumps({'kind': kind, 'folder': folder, 'clientId': clientId,
											'clientSecret': clientSecret}))
	return status(setup)
