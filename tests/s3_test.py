"""
Tests for lib/s3.py.

No network beyond loopback: the signing is checked against the vector AWS
publishes in its own SigV4 docs, and the client against a fake server that
plays the part of a bucket - storing PUTs in a dict, answering GET/HEAD/DELETE
the way S3 does - so what is under test is our reading of the spec, not
whichever real provider happens to be reachable today.
"""

import datetime
import hashlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from parity_deriva.lib import s3


class SigningTest(unittest.TestCase):
	"""AWS's own "Example: GET Object" vector from the SigV4 documentation."""

	def test_the_published_vector(self):
		when = datetime.datetime(2013, 5, 24, 0, 0, 0)
		headers = {
			'host': 'examplebucket.s3.amazonaws.com',
			'range': 'bytes=0-9',
			'x-amz-content-sha256':
				'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
			'x-amz-date': '20130524T000000Z',
		}
		auth = s3.authorization(
			'GET', 'examplebucket.s3.amazonaws.com', '/test.txt', None, headers,
			headers['x-amz-content-sha256'], 'AKIAIOSFODNN7EXAMPLE',
			'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'us-east-1', when)
		self.assertEqual(
			auth,
			'AWS4-HMAC-SHA256 '
			'Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, '
			'SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, '
			'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41')


class FakeS3Case(unittest.TestCase):
	"""A loopback server that plays the part of one bucket."""

	BUCKET = 'test-bucket'
	PREFIX = 'pfx'

	def setUp(self):
		store = {}
		errors = []
		state = {'bad_etag': False}
		bucket, prefix = self.BUCKET, self.PREFIX

		def check(handler):
			if not handler.headers.get('Authorization', '').startswith(
					'AWS4-HMAC-SHA256 Credential='):
				errors.append('missing/bad Authorization: %r' % handler.headers.get('Authorization'))
			if not handler.headers.get('x-amz-date'):
				errors.append('missing x-amz-date')
			if not handler.headers.get('x-amz-content-sha256'):
				errors.append('missing x-amz-content-sha256')
			if not handler.path.startswith('/%s/%s/' % (bucket, prefix)):
				errors.append('unexpected path %r' % handler.path)

		class Handler(BaseHTTPRequestHandler):

			def do_PUT(self):
				check(self)
				length = int(self.headers.get('Content-Length', 0))
				data = self.rfile.read(length)
				store[self.path] = data
				digest = 'not-the-md5' if state['bad_etag'] else hashlib.md5(data).hexdigest()
				self.send_response(200)
				self.send_header('ETag', '"%s"' % digest)
				self.end_headers()

			def do_GET(self):
				check(self)
				data = store.get(self.path)
				if data is None:
					self._notfound(body=True)
					return
				self.send_response(200)
				self.send_header('Content-Length', str(len(data)))
				self.end_headers()
				self.wfile.write(data)

			def do_HEAD(self):
				check(self)
				if self.path not in store:
					self._notfound(body=False)
					return
				self.send_response(200)
				self.end_headers()

			def do_DELETE(self):
				check(self)
				store.pop(self.path, None)
				self.send_response(204)
				self.end_headers()

			def _notfound(self, body):
				payload = b'<Error><Code>NoSuchKey</Code><Message>no such key</Message></Error>'
				self.send_response(404)
				if body:
					self.send_header('Content-Length', str(len(payload)))
				self.end_headers()
				if body:
					self.wfile.write(payload)

			def log_message(self, *args):
				pass

		self.store, self.errors, self.state = store, errors, state
		self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
		thread = threading.Thread(target=self.server.serve_forever, daemon=True)
		thread.start()
		self.addCleanup(self.server.shutdown)
		self.addCleanup(self.server.server_close)
		endpoint = 'http://127.0.0.1:%d' % self.server.server_address[1]
		self.bucket = s3.Bucket(endpoint, self.BUCKET, 'AKIAEXAMPLE', 'secretexample',
								prefix=self.PREFIX)

	def test_put_then_get_is_the_same_bytes(self):
		self.bucket.put('sweeps/x/1.json.gz', b'some bytes\x00\x01')
		self.assertEqual(self.bucket.get('sweeps/x/1.json.gz'), b'some bytes\x00\x01')
		self.assertEqual(self.errors, [])

	def test_exists(self):
		self.assertFalse(self.bucket.exists('sweeps/x/1.json.gz'))
		self.bucket.put('sweeps/x/1.json.gz', b'data')
		self.assertTrue(self.bucket.exists('sweeps/x/1.json.gz'))
		self.assertEqual(self.errors, [])

	def test_delete_then_missing(self):
		self.bucket.put('sweeps/x/1.json.gz', b'data')
		self.bucket.delete('sweeps/x/1.json.gz')
		self.assertFalse(self.bucket.exists('sweeps/x/1.json.gz'))
		self.bucket.delete('sweeps/x/1.json.gz')  # already gone, still fine

	def test_a_missing_key_is_a_404(self):
		with self.assertRaises(s3.S3Error) as raised:
			self.bucket.get('nothing/here.json.gz')
		self.assertEqual(raised.exception.status, 404)

	def test_a_wrong_etag_is_refused(self):
		self.state['bad_etag'] = True
		with self.assertRaises(s3.S3Error):
			self.bucket.put('sweeps/x/1.json.gz', b'data')

	def test_the_prefix_and_bucket_are_in_every_path(self):
		self.bucket.put('sweeps/x/1.json.gz', b'data')
		self.assertIn('/%s/%s/sweeps/x/1.json.gz' % (self.BUCKET, self.PREFIX), self.store)


class FromSettingsTest(unittest.TestCase):

	def test_unconfigured_is_none(self):
		setup = type('Setup', (), {})()
		self.assertIsNone(s3.fromSettings(setup))

	def test_partly_configured_is_still_none(self):
		setup = type('Setup', (), {'S3_ENDPOINT': 'http://127.0.0.1:9000',
								  'S3_BUCKET': 'b'})()
		self.assertIsNone(s3.fromSettings(setup))

	def test_configured(self):
		setup = type('Setup', (), {
			'S3_ENDPOINT': 'http://127.0.0.1:9000', 'S3_BUCKET': 'b',
			'S3_ACCESS_KEY': 'ak', 'S3_SECRET_KEY': 'sk', 'S3_PREFIX': 'p'})()
		bucket = s3.fromSettings(setup)
		self.assertIsInstance(bucket, s3.Bucket)
		self.assertEqual(bucket.region, 'us-east-1')
		self.assertEqual(bucket.prefix, 'p')


if __name__ == '__main__':
	unittest.main()
