"""
A client for one S3 bucket, signed by hand.

This exists to move simulation output ("cold storage") off the server's own
disk onto whichever S3-compatible object storage a deployment has - AWS,
Hetzner's Object Storage, Backblaze B2, a local MinIO for a test. Any of
those would mean adding boto3 (and its own dependency tree) for four HTTP
verbs, so this speaks the wire protocol itself: path-style URLs and Signature
Version 4, built from urllib.request, hmac, hashlib and datetime alone.

Keys are expected to be simple (sweeps/20260926-013243-577d39/12.json.gz) but
the percent-encoding follows the S3 canonical-request rules regardless.
"""
import datetime
import hashlib
import hmac
import re
import urllib.error
import urllib.parse
import urllib.request

#: generous - a cold-storage file can be large and the network is not local
TIMEOUT = 120


class S3Error(Exception):
	"""Raised for anything but a clean 2xx/404. .status is the HTTP status, or
	None when the server could not even be reached."""

	def __init__(self, message, status=None):
		super(S3Error, self).__init__(message)
		self.status = status


def uriEncode(value, safe=''):
	"""Percent-encode every byte but A-Za-z0-9-._~ (and whatever is in `safe`)."""
	return urllib.parse.quote(str(value), safe=safe)


def canonicalQueryString(query):
	"""Sorted, percent-encoded 'k=v&...' - S3 signs an empty query as ''."""
	if not query:
		return ''
	items = query.items() if isinstance(query, dict) else query
	pairs = sorted((uriEncode(k), uriEncode(v)) for k, v in items)
	return '&'.join('%s=%s' % pair for pair in pairs)


def signingKey(secretKey, datestamp, region, service):
	def sign(key, msg):
		return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
	kDate = sign(('AWS4' + secretKey).encode('utf-8'), datestamp)
	kRegion = sign(kDate, region)
	kService = sign(kRegion, service)
	return sign(kService, 'aws4_request')


def authorization(method, host, path, query, headers, payloadHash,
				  accessKey, secretKey, region, when, service='s3'):
	"""
	The Authorization header value for one request, AWS Signature Version 4.

	`headers` is whatever the caller intends to send (x-amz-date,
	x-amz-content-sha256, ...); `host` is added on top, and all of it is
	signed - there is no header sent unsigned. `path` is the raw, unencoded
	URI; this does the S3 canonical-URI encoding itself.
	"""
	amzdate = when.strftime('%Y%m%dT%H%M%SZ')
	datestamp = when.strftime('%Y%m%d')
	allHeaders = dict((k.lower(), v) for k, v in headers.items())
	allHeaders['host'] = host
	signedNames = sorted(allHeaders)
	canonicalHeaders = ''.join('%s:%s\n' % (name, allHeaders[name].strip())
							   for name in signedNames)
	signedHeaders = ';'.join(signedNames)
	canonicalRequest = '\n'.join([
		method, uriEncode(path, safe='/~'), canonicalQueryString(query),
		canonicalHeaders, signedHeaders, payloadHash])
	credentialScope = '%s/%s/%s/aws4_request' % (datestamp, region, service)
	stringToSign = '\n'.join([
		'AWS4-HMAC-SHA256', amzdate, credentialScope,
		hashlib.sha256(canonicalRequest.encode('utf-8')).hexdigest()])
	key = signingKey(secretKey, datestamp, region, service)
	signature = hmac.new(key, stringToSign.encode('utf-8'), hashlib.sha256).hexdigest()
	return ('AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s'
			% (accessKey, credentialScope, signedHeaders, signature))


def _errorMessage(status, body):
	"""status plus the <Code>/<Message> of an S3 XML error, or just the start of the body."""
	text = (body or b'').decode('utf-8', 'replace')
	code = re.search(r'<Code>([^<]*)</Code>', text)
	message = re.search(r'<Message>([^<]*)</Message>', text)
	if code or message:
		return '%s %s: %s' % (status, code.group(1) if code else '?',
							  message.group(1) if message else text[:200])
	return '%s: %s' % (status, text[:200])


class Bucket(object):
	"""One S3-compatible bucket, path-style, header-signed (not presigned)."""

	def __init__(self, endpoint, bucket, access_key, secret_key,
				region='us-east-1', prefix=''):
		self.endpoint = endpoint.rstrip('/')
		self.bucket = bucket
		self.access_key = access_key
		self.secret_key = secret_key
		self.region = region
		self.prefix = prefix.strip('/')

	def _path(self, key):
		full = '%s/%s' % (self.prefix, key) if self.prefix else key
		return '/%s/%s' % (self.bucket, full)

	def _request(self, method, key, data=None):
		parsed = urllib.parse.urlparse(self.endpoint)
		path = self._path(key)
		url = '%s://%s%s' % (parsed.scheme, parsed.netloc, uriEncode(path, safe='/~'))
		payloadHash = hashlib.sha256(data or b'').hexdigest()
		when = datetime.datetime.now(datetime.timezone.utc)
		headers = {'x-amz-content-sha256': payloadHash,
				  'x-amz-date': when.strftime('%Y%m%dT%H%M%SZ')}
		headers['Authorization'] = authorization(
			method, parsed.netloc, path, None, headers, payloadHash,
			self.access_key, self.secret_key, self.region, when)
		request = urllib.request.Request(url, data=data, headers=headers, method=method)
		try:
			with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
				# response.headers is case-insensitive (email.message.Message);
				# a plain dict would not be, and servers do not agree on the case
				return response.status, response.read(), response.headers
		except urllib.error.HTTPError as error:
			body = error.read()
			raise S3Error(_errorMessage(error.code, body), status=error.code)
		except urllib.error.URLError as error:
			raise S3Error(str(error.reason), status=None)

	def put(self, key, data):
		"""PUT `data` (bytes) at `key`; refuses a reply whose ETag disagrees with it."""
		# ponytail: the ETag is the MD5 on AWS (SSE-S3), Hetzner, B2, MinIO and
		# Garage; an SSE-KMS bucket's is not - send Content-MD5 instead if one is used
		digest = hashlib.md5(data).hexdigest()
		status, body, headers = self._request('PUT', key, data=data)
		etag = (headers.get('ETag') or '').strip('"')
		if etag != digest:
			raise S3Error("PUT %s: ETag %r does not match md5 %r" % (key, etag, digest),
						 status=status)

	def get(self, key):
		"""The bytes at `key`. S3Error.status == 404 when there is nothing there."""
		status, body, headers = self._request('GET', key)
		return body

	def exists(self, key):
		try:
			self._request('HEAD', key)
			return True
		except S3Error as error:
			if error.status == 404:
				return False
			raise

	def delete(self, key):
		"""204 (deleted) and 404 (already gone) are both a clean delete."""
		try:
			self._request('DELETE', key)
		except S3Error as error:
			if error.status != 404:
				raise


def fromSettings(setup):
	"""A Bucket built from setup.S3_*, or None when it is not configured."""
	endpoint = getattr(setup, 'S3_ENDPOINT', None)
	bucket = getattr(setup, 'S3_BUCKET', None)
	accessKey = getattr(setup, 'S3_ACCESS_KEY', None)
	secretKey = getattr(setup, 'S3_SECRET_KEY', None)
	if not endpoint or not bucket or not accessKey or not secretKey:
		return None
	return Bucket(endpoint, bucket, accessKey, secretKey,
				 region=getattr(setup, 'S3_REGION', None) or 'us-east-1',
				 prefix=getattr(setup, 'S3_PREFIX', None) or '')
