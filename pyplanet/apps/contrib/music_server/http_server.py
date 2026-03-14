import asyncio
import hashlib
import logging
import os
import secrets
import time

from aiohttp import web

logger = logging.getLogger(__name__)

UPLOAD_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Music Server Upload</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1a1a2e; color: #eee; min-height: 100vh; display: flex;
         align-items: center; justify-content: center; }
  .container { background: #16213e; border-radius: 12px; padding: 2rem;
               max-width: 500px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; color: #e94560; text-align: center; }
  .drop-zone { border: 2px dashed #0f3460; border-radius: 8px; padding: 2rem;
               text-align: center; cursor: pointer; transition: all 0.3s;
               margin-bottom: 1rem; }
  .drop-zone:hover, .drop-zone.drag-over { border-color: #e94560; background: #0f3460; }
  .drop-zone p { color: #888; margin-bottom: 0.5rem; }
  .drop-zone .formats { font-size: 0.8rem; color: #666; }
  input[type="file"] { display: none; }
  .btn { background: #e94560; color: white; border: none; padding: 0.75rem 1.5rem;
         border-radius: 6px; cursor: pointer; font-size: 1rem; width: 100%;
         transition: background 0.3s; }
  .btn:hover { background: #c73e54; }
  .btn:disabled { background: #555; cursor: not-allowed; }
  .status { margin-top: 1rem; padding: 0.75rem; border-radius: 6px; display: none;
            text-align: center; font-size: 0.9rem; }
  .status.success { display: block; background: #0a3d2a; color: #4ade80; }
  .status.error { display: block; background: #3d0a0a; color: #f87171; }
  .status.progress { display: block; background: #0f3460; color: #60a5fa; }
  .file-name { color: #e94560; font-size: 0.9rem; margin: 0.5rem 0; text-align: center; }
  .songs-list { margin-top: 1.5rem; border-top: 1px solid #0f3460; padding-top: 1rem; }
  .songs-list h2 { font-size: 1rem; color: #888; margin-bottom: 0.5rem; }
  .songs-list ul { list-style: none; max-height: 200px; overflow-y: auto; }
  .songs-list li { padding: 0.3rem 0; font-size: 0.85rem; color: #aaa;
                   border-bottom: 1px solid #0f3460; }
</style>
</head>
<body>
<div class="container">
  <h1>Music Server Upload</h1>
  <div class="drop-zone" id="dropZone">
    <p>Drag & drop audio file here</p>
    <p class="formats">or click to browse</p>
    <p class="formats">Accepts: mp3, ogg, flac, wav, m4a, aac, wma, opus</p>
  </div>
  <div class="file-name" id="fileName"></div>
  <button class="btn" id="uploadBtn" disabled>Upload</button>
  <div class="status" id="status"></div>
  <div class="songs-list" id="songsList"></div>
</div>
<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.createElement('input');
fileInput.type = 'file';
fileInput.accept = '.mp3,.ogg,.flac,.wav,.m4a,.aac,.wma,.opus';
const uploadBtn = document.getElementById('uploadBtn');
const status = document.getElementById('status');
const fileName = document.getElementById('fileName');
let selectedFile = null;

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault(); dropZone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) selectFile(fileInput.files[0]); });

function selectFile(file) {
  selectedFile = file;
  fileName.textContent = file.name + ' (' + (file.size / 1024 / 1024).toFixed(1) + ' MB)';
  uploadBtn.disabled = false;
  status.className = 'status'; status.style.display = 'none';
}

uploadBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  uploadBtn.disabled = true;
  status.className = 'status progress'; status.textContent = 'Uploading and converting...';
  const formData = new FormData();
  formData.append('file', selectedFile);
  try {
    const resp = await fetch('/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (resp.ok) {
      status.className = 'status success';
      status.textContent = 'Uploaded: ' + data.title + ' by ' + data.artist;
      selectedFile = null; fileName.textContent = ''; loadSongs();
    } else {
      status.className = 'status error'; status.textContent = data.error || 'Upload failed';
    }
  } catch (e) {
    status.className = 'status error'; status.textContent = 'Upload failed: ' + e.message;
  }
  uploadBtn.disabled = false;
});

async function loadSongs() {
  try {
    const resp = await fetch('/songs');
    const data = await resp.json();
    const list = document.getElementById('songsList');
    if (data.songs && data.songs.length) {
      list.innerHTML = '<h2>Songs (' + data.songs.length + ')</h2><ul>' +
        data.songs.map(s => '<li>' + s.title + ' - ' + s.artist + '</li>').join('') + '</ul>';
    }
  } catch(e) {}
}
loadSongs();
</script>
</body>
</html>'''

LOGIN_PAGE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Music Server Login</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1a1a2e; color: #eee; min-height: 100vh; display: flex;
         align-items: center; justify-content: center; }
  .container { background: #16213e; border-radius: 12px; padding: 2rem;
               max-width: 400px; width: 90%; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; color: #e94560; text-align: center; }
  input[type="password"] { width: 100%; padding: 0.75rem; border-radius: 6px;
         border: 1px solid #0f3460; background: #1a1a2e; color: #eee;
         font-size: 1rem; margin-bottom: 1rem; }
  .btn { background: #e94560; color: white; border: none; padding: 0.75rem 1.5rem;
         border-radius: 6px; cursor: pointer; font-size: 1rem; width: 100%; }
  .btn:hover { background: #c73e54; }
  .error { color: #f87171; text-align: center; margin-top: 0.5rem; font-size: 0.9rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Music Server</h1>
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button class="btn" type="submit">Login</button>
    {error}
  </form>
</div>
</body>
</html>'''

ALLOWED_EXTENSIONS = {'.mp3', '.ogg', '.flac', '.wav', '.m4a', '.aac', '.wma', '.opus'}
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB


class MusicHttpServer:
	"""Embedded HTTP server for serving .ogg music files with upload support."""

	def __init__(self):
		self.app = None
		self.runner = None
		self.site = None
		self.directory = None
		self.public_url = ''
		self.upload_password = ''
		self.ffmpeg_path = 'ffmpeg'
		self.on_song_uploaded = None  # Callback to add song to rotation.
		self._sessions = {}  # token -> expiry timestamp

	async def start(self, host, port, directory, public_url='', upload_password='',
					ffmpeg_path='ffmpeg', on_song_uploaded=None):
		"""Start the HTTP server."""
		self.directory = directory
		self.public_url = public_url.rstrip('/')
		self.upload_password = upload_password
		self.ffmpeg_path = ffmpeg_path
		self.on_song_uploaded = on_song_uploaded

		os.makedirs(directory, exist_ok=True)

		self.app = web.Application(client_max_size=MAX_UPLOAD_SIZE)
		self.app.router.add_get('/music/{filename}', self.handle_file)
		self.app.router.add_get('/health', self.handle_health)
		self.app.router.add_get('/upload', self.handle_upload_page)
		self.app.router.add_post('/upload', self.handle_upload)
		self.app.router.add_get('/login', self.handle_login_page)
		self.app.router.add_post('/login', self.handle_login)
		self.app.router.add_get('/songs', self.handle_songs_list)

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

	def _check_session(self, request):
		"""Check if request has a valid session cookie."""
		if not self.upload_password:
			return True  # No password set = no auth required.
		token = request.cookies.get('music_session', '')
		if token in self._sessions:
			if self._sessions[token] > time.time():
				return True
			del self._sessions[token]
		return False

	def _create_session(self):
		"""Create a new session token."""
		token = secrets.token_hex(32)
		self._sessions[token] = time.time() + 86400  # 24h expiry.
		return token

	# ---- Handlers ----

	async def handle_file(self, request):
		"""Serve an .ogg file from the music directory."""
		filename = request.match_info['filename']
		if '..' in filename or '/' in filename or '\\' in filename:
			return web.Response(status=403, text='Forbidden')
		if not filename.endswith('.ogg'):
			return web.Response(status=404, text='Not found')
		filepath = os.path.join(self.directory, filename)
		if not os.path.isfile(filepath):
			return web.Response(status=404, text='Not found')
		return web.FileResponse(filepath, headers={'Content-Type': 'audio/ogg'})

	async def handle_health(self, request):
		return web.Response(text='ok')

	async def handle_login_page(self, request):
		return web.Response(
			text=LOGIN_PAGE_HTML.replace('{error}', ''),
			content_type='text/html')

	async def handle_login(self, request):
		data = await request.post()
		password = data.get('password', '')
		if password == self.upload_password:
			token = self._create_session()
			resp = web.HTTPFound('/upload')
			resp.set_cookie('music_session', token, max_age=86400, httponly=True, samesite='Strict')
			return resp
		return web.Response(
			text=LOGIN_PAGE_HTML.replace('{error}', '<p class="error">Wrong password</p>'),
			content_type='text/html')

	async def handle_upload_page(self, request):
		if not self._check_session(request):
			raise web.HTTPFound('/login')
		return web.Response(text=UPLOAD_PAGE_HTML, content_type='text/html')

	async def handle_upload(self, request):
		if not self._check_session(request):
			return web.json_response({'error': 'Unauthorized'}, status=401)

		reader = await request.multipart()
		field = await reader.next()
		if not field or field.name != 'file':
			return web.json_response({'error': 'No file provided'}, status=400)

		original_name = field.filename or 'unknown'
		ext = os.path.splitext(original_name)[1].lower()
		if ext not in ALLOWED_EXTENSIONS:
			return web.json_response(
				{'error': 'Unsupported format. Allowed: {}'.format(', '.join(sorted(ALLOWED_EXTENSIONS)))},
				status=400)

		# Save uploaded file to temp location.
		safe_name = hashlib.md5('{}{}'.format(original_name, time.time()).encode()).hexdigest()
		temp_path = os.path.join(self.directory, '{}{}'.format(safe_name, ext))

		size = 0
		with open(temp_path, 'wb') as f:
			while True:
				chunk = await field.read_chunk()
				if not chunk:
					break
				size += len(chunk)
				if size > MAX_UPLOAD_SIZE:
					f.close()
					os.remove(temp_path)
					return web.json_response({'error': 'File too large (max 100MB)'}, status=400)
				f.write(chunk)

		# Convert to .ogg if not already.
		ogg_path = os.path.join(self.directory, '{}.ogg'.format(safe_name))
		try:
			if ext == '.ogg':
				os.rename(temp_path, ogg_path)
			else:
				proc = await asyncio.create_subprocess_exec(
					self.ffmpeg_path, '-i', temp_path,
					'-vn',  # Strip video/cover art streams.
					'-c:a', 'libvorbis', '-q:a', '4',
					'-y', ogg_path,
					stdout=asyncio.subprocess.PIPE,
					stderr=asyncio.subprocess.PIPE,
				)
				_, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
				if proc.returncode != 0:
					logger.error('ffmpeg conversion failed: %s', stderr.decode()[:500])
					return web.json_response({'error': 'Conversion failed'}, status=500)
				# Remove temp file.
				if os.path.exists(temp_path):
					os.remove(temp_path)
		except asyncio.TimeoutError:
			return web.json_response({'error': 'Conversion timed out'}, status=500)
		except Exception as e:
			logger.error('Upload processing failed: %s', e)
			return web.json_response({'error': 'Processing failed'}, status=500)

		# Extract metadata.
		from tinytag import TinyTag
		tag_mapping = {
			'album': 'album', 'albumartist': 'albumartist', 'title': 'title',
			'artist': 'artist', 'date': 'year', 'tracknumber': 'track',
			'discnumber': 'disc', 'genre': 'genre',
		}
		tags = {}
		try:
			ogg = TinyTag.get(ogg_path)
			for key, attr in tag_mapping.items():
				value = getattr(ogg, attr, None)
				tags[key] = value if value else 'Unknown'
		except Exception:
			tags = {k: 'Unknown' for k in tag_mapping}

		# Use original filename for title if metadata is unknown.
		if tags.get('title') == 'Unknown':
			tags['title'] = os.path.splitext(original_name)[0]

		# Build public URL and add to rotation.
		filename = '{}.ogg'.format(safe_name)
		song_url = self.get_public_url(filename)

		if self.on_song_uploaded:
			await self.on_song_uploaded(song_url, tags)

		logger.info('Uploaded: %s by %s (%s)', tags.get('title'), tags.get('artist'), filename)

		return web.json_response({
			'ok': True,
			'title': tags.get('title', 'Unknown'),
			'artist': tags.get('artist', 'Unknown'),
			'url': song_url,
		})

	async def handle_songs_list(self, request):
		"""Return list of songs as JSON (for the upload page)."""
		if not self._check_session(request):
			return web.json_response({'error': 'Unauthorized'}, status=401)

		songs = []
		for f in sorted(os.listdir(self.directory)):
			if f.endswith('.ogg'):
				from tinytag import TinyTag
				filepath = os.path.join(self.directory, f)
				try:
					ogg = TinyTag.get(filepath)
					songs.append({
						'title': ogg.title or f,
						'artist': ogg.artist or 'Unknown',
						'filename': f,
					})
				except Exception:
					songs.append({'title': f, 'artist': 'Unknown', 'filename': f})

		return web.json_response({'songs': songs})
