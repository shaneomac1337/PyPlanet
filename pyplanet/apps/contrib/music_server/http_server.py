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
<title>Music Server</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1a1a2e; color: #eee; min-height: 100vh; padding: 2rem; }
  .container { background: #16213e; border-radius: 12px; padding: 2rem;
               max-width: 600px; margin: 0 auto; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
  h1 { font-size: 1.4rem; margin-bottom: 1.5rem; color: #e94560; text-align: center; }
  h2 { font-size: 1.1rem; margin: 1.5rem 0 0.75rem; color: #e94560; }
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
  .btn-sm { padding: 0.4rem 0.8rem; font-size: 0.8rem; width: auto; }
  .btn-save { background: #0f9b58; }
  .btn-save:hover { background: #0b7a45; }
  .btn-cancel { background: #555; }
  .status { margin-top: 1rem; padding: 0.75rem; border-radius: 6px; display: none;
            text-align: center; font-size: 0.9rem; }
  .status.success { display: block; background: #0a3d2a; color: #4ade80; }
  .status.error { display: block; background: #3d0a0a; color: #f87171; }
  .status.progress { display: block; background: #0f3460; color: #60a5fa; }
  .file-name { color: #e94560; font-size: 0.9rem; margin: 0.5rem 0; text-align: center; }
  .songs-list { margin-top: 1.5rem; border-top: 1px solid #0f3460; padding-top: 1rem; }
  .songs-list ul { list-style: none; max-height: 400px; overflow-y: auto; }
  .songs-list li { padding: 0.5rem 0.5rem; font-size: 0.85rem; color: #aaa;
                   border-bottom: 1px solid #0f3460; display: flex;
                   justify-content: space-between; align-items: center; }
  .songs-list li:hover { background: #0f3460; }
  .song-info { flex: 1; cursor: pointer; }
  .song-title { color: #eee; }
  .song-artist { color: #888; font-size: 0.8rem; }
  .edit-form { display: none; background: #0f3460; border-radius: 8px; padding: 1rem;
               margin-top: 0.5rem; }
  .edit-form.active { display: block; }
  .edit-form label { display: block; font-size: 0.8rem; color: #888; margin-top: 0.5rem; }
  .edit-form input { width: 100%; padding: 0.5rem; border-radius: 4px;
                     border: 1px solid #1a1a2e; background: #16213e; color: #eee;
                     font-size: 0.9rem; margin-top: 0.2rem; }
  .edit-buttons { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Music Server</h1>

  <div class="drop-zone" id="dropZone">
    <p>Drag & drop audio file here</p>
    <p class="formats">or click to browse</p>
    <p class="formats">Accepts: mp3, ogg, flac, wav, m4a, aac, wma, opus</p>
  </div>
  <div class="file-name" id="fileName"></div>
  <button class="btn" id="uploadBtn" disabled>Upload</button>
  <div class="status" id="status"></div>

  <div class="songs-list" id="songsList"></div>

  <div class="edit-form" id="editForm">
    <h2>Edit Song Tags</h2>
    <input type="hidden" id="editFilename">
    <label>Title</label>
    <input type="text" id="editTitle">
    <label>Artist</label>
    <input type="text" id="editArtist">
    <label>Album</label>
    <input type="text" id="editAlbum">
    <label>Genre</label>
    <input type="text" id="editGenre">
    <div class="edit-buttons">
      <button class="btn btn-sm btn-save" onclick="saveTags()">Save</button>
      <button class="btn btn-sm btn-cancel" onclick="closeEditor()">Cancel</button>
    </div>
    <div class="status" id="editStatus"></div>
  </div>
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
        data.songs.map(s =>
          '<li><div class="song-info" onclick="openEditor(\\'' + s.filename + '\\', \\'' +
          esc(s.title) + '\\', \\'' + esc(s.artist) + '\\', \\'' + esc(s.album || '') +
          '\\', \\'' + esc(s.genre || '') + '\\')">' +
          '<span class="song-title">' + esc(s.title) + '</span>' +
          '<br><span class="song-artist">' + esc(s.artist) + '</span>' +
          '</div></li>'
        ).join('') + '</ul>';
    } else {
      list.innerHTML = '<h2>No songs yet</h2>';
    }
  } catch(e) {}
}

function esc(s) { return (s||'').replace(/'/g, "\\\\'").replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function openEditor(filename, title, artist, album, genre) {
  document.getElementById('editFilename').value = filename;
  document.getElementById('editTitle').value = title.replace(/\\\\'/g, "'");
  document.getElementById('editArtist').value = artist.replace(/\\\\'/g, "'");
  document.getElementById('editAlbum').value = album.replace(/\\\\'/g, "'");
  document.getElementById('editGenre').value = genre.replace(/\\\\'/g, "'");
  document.getElementById('editForm').classList.add('active');
  document.getElementById('editStatus').className = 'status';
  document.getElementById('editStatus').style.display = 'none';
}

function closeEditor() {
  document.getElementById('editForm').classList.remove('active');
}

async function saveTags() {
  const filename = document.getElementById('editFilename').value;
  const tags = {
    title: document.getElementById('editTitle').value,
    artist: document.getElementById('editArtist').value,
    album: document.getElementById('editAlbum').value,
    genre: document.getElementById('editGenre').value,
  };
  const editStatus = document.getElementById('editStatus');
  editStatus.className = 'status progress'; editStatus.textContent = 'Saving...';
  try {
    const resp = await fetch('/tags/' + filename, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(tags),
    });
    const data = await resp.json();
    if (resp.ok) {
      editStatus.className = 'status success'; editStatus.textContent = 'Tags saved!';
      setTimeout(() => { closeEditor(); loadSongs(); }, 1000);
    } else {
      editStatus.className = 'status error'; editStatus.textContent = data.error || 'Save failed';
    }
  } catch (e) {
    editStatus.className = 'status error'; editStatus.textContent = 'Save failed: ' + e.message;
  }
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
	"""Embedded HTTP server for serving .ogg music files with upload and tag editing."""

	def __init__(self):
		self.app = None
		self.runner = None
		self.site = None
		self.directory = None
		self.public_url = ''
		self.upload_password = ''
		self.ffmpeg_path = 'ffmpeg'
		self.volume_boost_db = 0
		self.on_song_uploaded = None
		self.on_tags_updated = None  # Callback when tags are edited.
		self._sessions = {}

	async def start(self, host, port, directory, public_url='', upload_password='',
					ffmpeg_path='ffmpeg', volume_boost_db=0, on_song_uploaded=None, on_tags_updated=None):
		"""Start the HTTP server."""
		self.directory = directory
		self.public_url = public_url.rstrip('/')
		self.upload_password = upload_password
		self.ffmpeg_path = ffmpeg_path
		self.volume_boost_db = volume_boost_db
		self.on_song_uploaded = on_song_uploaded
		self.on_tags_updated = on_tags_updated

		os.makedirs(directory, exist_ok=True)

		self.app = web.Application(client_max_size=MAX_UPLOAD_SIZE)
		self.app.router.add_get('/music/{filename}', self.handle_file)
		self.app.router.add_get('/health', self.handle_health)
		self.app.router.add_get('/upload', self.handle_upload_page)
		self.app.router.add_post('/upload', self.handle_upload)
		self.app.router.add_get('/login', self.handle_login_page)
		self.app.router.add_post('/login', self.handle_login)
		self.app.router.add_get('/songs', self.handle_songs_list)
		self.app.router.add_post('/tags/{filename}', self.handle_edit_tags)

		self.runner = web.AppRunner(self.app)
		await self.runner.setup()
		self.site = web.TCPSite(self.runner, host, port)
		await self.site.start()

		logger.info('Music HTTP server started on %s:%d (serving from %s)', host, port, directory)

	async def stop(self):
		if self.runner:
			await self.runner.cleanup()
			self.runner = None
			self.site = None
			self.app = None
			logger.info('Music HTTP server stopped')

	def get_public_url(self, filename):
		if self.public_url:
			return '{}/music/{}'.format(self.public_url, filename)
		return '/music/{}'.format(filename)

	def _check_session(self, request):
		if not self.upload_password:
			return True
		token = request.cookies.get('music_session', '')
		if token in self._sessions:
			if self._sessions[token] > time.time():
				return True
			del self._sessions[token]
		return False

	def _create_session(self):
		token = secrets.token_hex(32)
		self._sessions[token] = time.time() + 86400
		return token

	# ---- Handlers ----

	async def handle_file(self, request):
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

		ogg_path = os.path.join(self.directory, '{}.ogg'.format(safe_name))
		try:
			if ext == '.ogg':
				os.rename(temp_path, ogg_path)
			else:
				proc = await asyncio.create_subprocess_exec(
					self.ffmpeg_path, '-i', temp_path,
					'-vn',
					'-c:a', 'libvorbis', '-q:a', '4',
					'-y', ogg_path,
					stdout=asyncio.subprocess.PIPE,
					stderr=asyncio.subprocess.PIPE,
				)
				_, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
				if proc.returncode != 0:
					logger.error('ffmpeg conversion failed: %s', stderr.decode()[:500])
					return web.json_response({'error': 'Conversion failed'}, status=500)
				if os.path.exists(temp_path):
					os.remove(temp_path)
		except asyncio.TimeoutError:
			return web.json_response({'error': 'Conversion timed out'}, status=500)
		except Exception as e:
			logger.error('Upload processing failed: %s', e)
			return web.json_response({'error': 'Processing failed'}, status=500)

		# Boost volume if configured.
		if self.volume_boost_db and self.volume_boost_db != 0:
			from .youtube import boost_volume
			await boost_volume(ogg_path, self.volume_boost_db, self.ffmpeg_path)

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

		if tags.get('title') == 'Unknown':
			tags['title'] = os.path.splitext(original_name)[0]

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
						'album': ogg.album or 'Unknown',
						'genre': getattr(ogg, 'genre', '') or '',
						'filename': f,
					})
				except Exception:
					songs.append({'title': f, 'artist': 'Unknown', 'album': '', 'genre': '', 'filename': f})

		return web.json_response({'songs': songs})

	async def handle_edit_tags(self, request):
		"""Edit tags on an .ogg file and update in-memory song list."""
		if not self._check_session(request):
			return web.json_response({'error': 'Unauthorized'}, status=401)

		filename = request.match_info['filename']
		if '..' in filename or '/' in filename or '\\' in filename:
			return web.json_response({'error': 'Forbidden'}, status=403)
		if not filename.endswith('.ogg'):
			return web.json_response({'error': 'Invalid file'}, status=400)

		filepath = os.path.join(self.directory, filename)
		if not os.path.isfile(filepath):
			return web.json_response({'error': 'File not found'}, status=404)

		try:
			data = await request.json()
		except Exception:
			return web.json_response({'error': 'Invalid JSON'}, status=400)

		title = data.get('title', '').strip()
		artist = data.get('artist', '').strip()
		album = data.get('album', '').strip()
		genre = data.get('genre', '').strip()

		if not title:
			return web.json_response({'error': 'Title is required'}, status=400)

		# Write tags to file using ffmpeg.
		temp_path = filepath + '.tmp.ogg'
		cmd = [
			self.ffmpeg_path, '-i', filepath, '-vn', '-c:a', 'copy',
			'-metadata', 'title={}'.format(title),
			'-metadata', 'artist={}'.format(artist),
			'-metadata', 'album={}'.format(album),
			'-metadata', 'genre={}'.format(genre),
			'-y', temp_path,
		]
		try:
			proc = await asyncio.create_subprocess_exec(
				*cmd,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
			)
			await asyncio.wait_for(proc.communicate(), timeout=30)
			if proc.returncode == 0 and os.path.isfile(temp_path):
				os.replace(temp_path, filepath)
			else:
				if os.path.exists(temp_path):
					os.remove(temp_path)
				return web.json_response({'error': 'Failed to write tags'}, status=500)
		except Exception as e:
			if os.path.exists(temp_path):
				os.remove(temp_path)
			return web.json_response({'error': 'Tag write error: {}'.format(str(e))}, status=500)

		# Update in-memory song list.
		new_tags = {
			'title': title or 'Unknown',
			'artist': artist or 'Unknown',
			'album': album or 'Unknown',
			'genre': genre or 'Unknown',
			'albumartist': 'Unknown',
			'date': 'Unknown',
			'tracknumber': 'Unknown',
			'discnumber': 'Unknown',
		}

		song_url = self.get_public_url(filename)
		if self.on_tags_updated:
			await self.on_tags_updated(song_url, new_tags)

		logger.info('Tags updated: %s -> %s by %s', filename, title, artist)
		return web.json_response({'ok': True, 'title': title, 'artist': artist})
