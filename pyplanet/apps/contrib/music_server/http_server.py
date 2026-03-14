import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)


class MusicHttpServer:
	"""Embedded HTTP server for serving .ogg music files."""

	def __init__(self):
		self.app = None
		self.runner = None
		self.site = None
		self.directory = None
		self.public_url = ''

	async def start(self, host, port, directory, public_url=''):
		"""Start the HTTP server.

		:param host: Bind address (e.g. '0.0.0.0').
		:param port: Bind port (e.g. 8080).
		:param directory: Directory to serve .ogg files from.
		:param public_url: Public URL prefix for constructing file URLs.
		"""
		self.directory = directory
		self.public_url = public_url.rstrip('/')

		os.makedirs(directory, exist_ok=True)

		self.app = web.Application()
		self.app.router.add_get('/music/{filename}', self.handle_file)
		self.app.router.add_get('/health', self.handle_health)

		self.runner = web.AppRunner(self.app)
		await self.runner.setup()
		self.site = web.TCPSite(self.runner, host, port)
		await self.site.start()

		logger.info('Music HTTP server started on %s:%d (serving from %s)', host, port, directory)

	async def stop(self):
		"""Stop the HTTP server gracefully."""
		if self.runner:
			await self.runner.cleanup()
			self.runner = None
			self.site = None
			self.app = None
			logger.info('Music HTTP server stopped')

	def get_public_url(self, filename):
		"""Get the public URL for a served file."""
		if self.public_url:
			return '{}/music/{}'.format(self.public_url, filename)
		return '/music/{}'.format(filename)

	async def handle_file(self, request):
		"""Serve an .ogg file from the music directory."""
		filename = request.match_info['filename']

		# Security: prevent path traversal.
		if '..' in filename or '/' in filename or '\\' in filename:
			return web.Response(status=403, text='Forbidden')

		if not filename.endswith('.ogg'):
			return web.Response(status=404, text='Not found')

		filepath = os.path.join(self.directory, filename)
		if not os.path.isfile(filepath):
			return web.Response(status=404, text='Not found')

		return web.FileResponse(filepath, headers={
			'Content-Type': 'audio/ogg',
		})

	async def handle_health(self, request):
		"""Health check endpoint."""
		return web.Response(text='ok')
