import asyncio
import logging
import os
import random
import async_timeout
import aiohttp

from io import BytesIO
from tinytag import TinyTag

from pyplanet.conf import settings
from pyplanet.apps.config import AppConfig
from pyplanet.apps.core.maniaplanet import callbacks as mp_signals
from pyplanet.contrib.command import Command
from pyplanet.contrib.setting import Setting
from .view import MusicListView, PlaylistView, FavoritesListView

logger = logging.getLogger(__name__)


class MusicServer(AppConfig):
	game_dependencies = ['trackmania', 'shootmania', 'trackmania_next']
	app_dependencies = ['core.maniaplanet', 'core.pyplanet']

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.lock = asyncio.Lock()
		self.context.signals.listen(mp_signals.map.map_end, self.map_end)
		self.server = None
		self.current_song_index = 0
		self.current_song = None
		self.songs = []
		self.list_view = None
		self.playlist = []
		self.playlist_view = None

		# Shuffle state.
		self.shuffle_order = []
		self.shuffle_index = 0

		# Vote-skip state.
		self.skip_votes = set()
		self.skip_vote_task = None

		# Feature availability flags (set during on_start).
		self.yt_available = False
		self.http_server = None

		# Settings.
		self.setting_override_map_music = Setting(
			'override_map_music', 'Override map music', Setting.CAT_BEHAVIOUR, type=bool,
			description='Whether to override the map music.',
			default=True,
		)
		self.setting_shuffle_enabled = Setting(
			'shuffle_enabled', 'Shuffle mode', Setting.CAT_BEHAVIOUR, type=bool,
			description='Enable shuffle mode for song rotation.',
			default=False,
		)
		self.setting_skip_vote_enabled = Setting(
			'skip_vote_enabled', 'Enable vote-skip', Setting.CAT_BEHAVIOUR, type=bool,
			description='Allow players to vote to skip the current song.',
			default=True,
		)
		self.setting_skip_vote_threshold = Setting(
			'skip_vote_threshold', 'Skip vote threshold', Setting.CAT_BEHAVIOUR, type=float,
			description='Fraction of online players needed to skip (0.0 - 1.0).',
			default=0.5,
		)
		self.setting_skip_vote_timeout = Setting(
			'skip_vote_timeout', 'Skip vote timeout', Setting.CAT_BEHAVIOUR, type=int,
			description='Seconds before a skip vote expires.',
			default=30,
		)
		self.setting_favorites_enabled = Setting(
			'favorites_enabled', 'Enable favorites', Setting.CAT_BEHAVIOUR, type=bool,
			description='Allow players to favorite songs.',
			default=True,
		)
		self.setting_http_enabled = Setting(
			'http_enabled', 'Enable HTTP server', Setting.CAT_BEHAVIOUR, type=bool,
			description='Enable the embedded HTTP file server for serving music files.',
			default=False,
		)
		self.setting_http_host = Setting(
			'http_host', 'HTTP server host', Setting.CAT_BEHAVIOUR, type=str,
			description='Bind address for the HTTP music server.',
			default='0.0.0.0',
		)
		self.setting_http_port = Setting(
			'http_port', 'HTTP server port', Setting.CAT_BEHAVIOUR, type=int,
			description='Port for the HTTP music server.',
			default=8080,
		)
		self.setting_http_public_url = Setting(
			'http_public_url', 'HTTP public URL', Setting.CAT_BEHAVIOUR, type=str,
			description='Public URL prefix for served files (e.g. http://myserver.com:8080).',
			default='',
		)
		self.setting_yt_enabled = Setting(
			'yt_enabled', 'Enable YouTube', Setting.CAT_BEHAVIOUR, type=bool,
			description='Enable YouTube integration via yt-dlp.',
			default=True,
		)
		self.setting_yt_dlp_path = Setting(
			'yt_dlp_path', 'yt-dlp path', Setting.CAT_BEHAVIOUR, type=str,
			description='Path to the yt-dlp binary.',
			default='yt-dlp',
		)
		self.setting_ffmpeg_path = Setting(
			'ffmpeg_path', 'ffmpeg path', Setting.CAT_BEHAVIOUR, type=str,
			description='Path to the ffmpeg binary.',
			default='ffmpeg',
		)
		self.setting_yt_download_dir = Setting(
			'yt_download_dir', 'Download directory', Setting.CAT_BEHAVIOUR, type=str,
			description='Directory for downloaded YouTube audio files.',
			default='./music',
		)
		self.setting_yt_max_duration = Setting(
			'yt_max_duration', 'Max YT duration', Setting.CAT_BEHAVIOUR, type=int,
			description='Maximum YouTube song duration in seconds.',
			default=600,
		)
		self.setting_yt_max_filesize = Setting(
			'yt_max_filesize', 'Max YT filesize', Setting.CAT_BEHAVIOUR, type=str,
			description='Maximum YouTube file size (e.g. 50M).',
			default='50M',
		)
		self.setting_yt_cleanup_after_days = Setting(
			'yt_cleanup_after_days', 'YT cleanup days', Setting.CAT_BEHAVIOUR, type=int,
			description='Auto-delete downloaded files after N days (0 = never).',
			default=7,
		)
		self.setting_upload_password = Setting(
			'upload_password', 'Upload password', Setting.CAT_BEHAVIOUR, type=str,
			description='Password for the web upload page. Leave empty for no auth.',
			default='',
		)

	async def on_start(self):
		# Load songs from settings.
		self.songs = await self.get_songs()

		# Initialize views.
		self.list_view = MusicListView(self)
		self.playlist_view = PlaylistView(self)

		# Register all settings.
		await self.context.setting.register(
			self.setting_override_map_music,
			self.setting_shuffle_enabled,
			self.setting_skip_vote_enabled,
			self.setting_skip_vote_threshold,
			self.setting_skip_vote_timeout,
			self.setting_favorites_enabled,
			self.setting_http_enabled,
			self.setting_http_host,
			self.setting_http_port,
			self.setting_http_public_url,
			self.setting_yt_enabled,
			self.setting_yt_dlp_path,
			self.setting_ffmpeg_path,
			self.setting_yt_download_dir,
			self.setting_yt_max_duration,
			self.setting_yt_max_filesize,
			self.setting_yt_cleanup_after_days,
			self.setting_upload_password,
		)

		# Register base permissions.
		await self.instance.permission_manager.register('play', 'Plays a song from the playlist', app=self, min_level=1)
		await self.instance.permission_manager.register('clear', 'Clear the playlist', app=self, min_level=1)
		await self.instance.permission_manager.register('shuffle', 'Toggle shuffle mode', app=self, min_level=1)
		await self.instance.permission_manager.register('nextsong', 'Force next song mid-map', app=self, min_level=1)

		# Register base commands.
		await self.instance.command_manager.register(
			Command(command='play', target=self.play_song, perms='music_server:play', admin=True)
				.add_param(name='songname', type=str, required=True),
			Command(command='song', target=self.get_current_song, admin=False),
			Command(command='songlist', aliases='musiclist', target=self.song_list, admin=False),
			Command(command='playlist', target=self.show_playlist, admin=False),
			Command(command='clearplaylist', target=self.clear_playlist, perms='music_server:clear', admin=True),
			Command(command='shuffle', target=self.toggle_shuffle, perms='music_server:shuffle', admin=True),
			Command(command='nextsong', target=self.force_next_song, perms='music_server:nextsong', admin=True),
		)

		# Register vote-skip if enabled.
		skip_enabled = await self.setting_skip_vote_enabled.get_value()
		if skip_enabled:
			await self.instance.command_manager.register(
				Command(command='skipmusic', target=self.vote_skip, admin=False),
			)

		# Register favorites if enabled.
		favorites_enabled = await self.setting_favorites_enabled.get_value()
		if favorites_enabled:
			await self.instance.permission_manager.register('topfavs', 'View top favorites', app=self, min_level=1)
			await self.instance.command_manager.register(
				Command(command='fav', target=self.add_favorite, admin=False),
				Command(command='unfav', target=self.remove_favorite, admin=False),
				Command(command='favlist', target=self.show_favorites, admin=False),
				Command(command='topfavs', target=self.show_top_favorites, perms='music_server:topfavs', admin=True),
			)

		# Probe and start YouTube integration.
		await self._init_youtube()

		# Probe and start HTTP server.
		await self._init_http_server()

		# Load any previously uploaded/downloaded songs from disk.
		await self._load_songs_from_disk()

		# Cleanup old YouTube downloads.
		cleanup_days = await self.setting_yt_cleanup_after_days.get_value()
		if cleanup_days > 0:
			download_dir = await self.setting_yt_download_dir.get_value()
			from .youtube import cleanup_old_files
			cleanup_old_files(download_dir, cleanup_days)

		self.current_song_index = -1

	async def on_stop(self):
		if self.http_server:
			await self.http_server.stop()
			self.http_server = None

	async def _init_youtube(self):
		"""Probe for yt-dlp and ffmpeg, register YouTube commands if available."""
		yt_enabled = await self.setting_yt_enabled.get_value()
		if not yt_enabled:
			logger.info('[Music] YouTube integration disabled by setting.')
			return

		from .youtube import check_binary

		yt_dlp_path = await self.setting_yt_dlp_path.get_value()
		ffmpeg_path = await self.setting_ffmpeg_path.get_value()

		yt_dlp_ok = await check_binary(yt_dlp_path)
		ffmpeg_ok = await check_binary(ffmpeg_path)

		if not yt_dlp_ok:
			logger.warning('[Music] yt-dlp not found at "%s" — YouTube features disabled.', yt_dlp_path)
		if not ffmpeg_ok:
			logger.warning('[Music] ffmpeg not found at "%s" — YouTube features disabled.', ffmpeg_path)

		if yt_dlp_ok and ffmpeg_ok:
			self.yt_available = True
			await self.instance.permission_manager.register('ytplay', 'Download and queue YouTube audio', app=self, min_level=1)
			await self.instance.command_manager.register(
				Command(command='ytplay', target=self.yt_play, perms='music_server:ytplay', admin=True)
					.add_param(name='url', type=str, required=True),
			)
			logger.info('[Music] YouTube integration enabled (yt-dlp: %s, ffmpeg: %s).', yt_dlp_path, ffmpeg_path)
		else:
			self.yt_available = False

	async def _init_http_server(self):
		"""Start the embedded HTTP server if enabled."""
		http_enabled = await self.setting_http_enabled.get_value()
		if not http_enabled:
			logger.info('[Music] HTTP file server disabled by setting.')
			return

		from .http_server import MusicHttpServer

		host = await self.setting_http_host.get_value()
		port = await self.setting_http_port.get_value()
		public_url = await self.setting_http_public_url.get_value()
		download_dir = await self.setting_yt_download_dir.get_value()

		upload_password = await self.setting_upload_password.get_value()
		ffmpeg_path = await self.setting_ffmpeg_path.get_value()

		try:
			self.http_server = MusicHttpServer()
			await self.http_server.start(
				host, port, download_dir, public_url,
				upload_password=upload_password,
				ffmpeg_path=ffmpeg_path,
				on_song_uploaded=self._on_song_uploaded,
				on_tags_updated=self._on_tags_updated,
			)
		except Exception as e:
			logger.error('[Music] Failed to start HTTP server: %s', e)
			self.http_server = None

	async def _load_songs_from_disk(self):
		"""Load existing .ogg files from the music download directory into rotation."""
		download_dir = await self.setting_yt_download_dir.get_value()
		if not os.path.isdir(download_dir):
			return

		# Get public URL prefix (need HTTP server to be initialized first, but settings are available).
		public_url = await self.setting_http_public_url.get_value()
		if not public_url:
			return

		public_url = public_url.rstrip('/')
		existing_urls = {song[0] for song in self.songs}
		loaded = 0

		from .youtube import get_file_tags

		for filename in sorted(os.listdir(download_dir)):
			if not filename.endswith('.ogg'):
				continue
			song_url = '{}/music/{}'.format(public_url, filename)
			if song_url in existing_urls:
				continue
			filepath = os.path.join(download_dir, filename)
			tags = get_file_tags(filepath)
			if tags.get('title') == 'Unknown':
				tags['title'] = os.path.splitext(filename)[0]
			self.songs.append((song_url, tags))
			loaded += 1

		if loaded:
			logger.info('[Music] Loaded %d songs from disk.', loaded)

	async def _on_tags_updated(self, song_url, tags):
		"""Callback from HTTP server when tags are edited via web UI."""
		for i, (url, _) in enumerate(self.songs):
			if url == song_url:
				self.songs[i] = (url, tags)
				logger.info('[Music] Tags updated in rotation: %s by %s', tags.get('title'), tags.get('artist'))
				return

	async def _on_song_uploaded(self, song_url, tags):
		"""Callback from HTTP server when a song is uploaded via web UI."""
		self.songs.append((song_url, tags))
		logger.info('[Music] Web upload added to rotation: %s by %s', tags.get('title'), tags.get('artist'))

	# ---- Song list & playlist ----

	async def song_list(self, player, *args, **kwargs):
		self.list_view = MusicListView(self)
		await self.list_view.display(player=player.login)

	async def insert_song(self, player, song):
		self.playlist = self.playlist + [{'player': player, 'song': song}]

	async def show_playlist(self, player, *args, **kwargs):
		self.playlist_view = PlaylistView(self)
		await self.playlist_view.display(player=player.login)

	async def add_to_playlist(self, player, song_index):
		async with self.lock:
			new_song = self.songs[song_index]
			if player.level == 0 and any(item['player'].login == player.login for item in self.playlist):
				message = '$i$f00You already have a song in the playlist! Wait till it\'s been played before adding another.'
				await self.instance.chat(message, player)
				return

			if not any(item['song'] == new_song for item in self.playlist):
				await self.insert_song(player, new_song)
				message = '$fff{}$z$s$fa0 was added to the playlist by $fff{}$z$s$fa0.'\
					.format(new_song[1]['artist'] + " - " + new_song[1]['title'], player.nickname)
				await self.instance.chat(message)
			else:
				message = '$i$f00This song has already been added to the playlist, pick another one.'
				await self.instance.chat(message, player)

	async def drop_from_playlist(self, player, song_info):
		async with self.lock:
			drop_song = next((item for item in self.playlist if item['song'][1]['title'] == song_info['song_name']), None)
			if drop_song is None:
				return
			if player.level == 0 and drop_song['player'].login != player.login:
				message = '$i$f00You can only drop your own queued songs!'
				await self.instance.chat(message, player)
			else:
				self.playlist.remove(drop_song)
				message = '$fff{}$z$s$fa0 dropped $fff{}$z$s$fa0 from the playlist.'\
					.format(player.nickname, song_info['song_name'])
				await self.instance.chat(message)

	# ---- Playback engine ----

	def _get_next_song_index(self):
		"""Get the next song index based on shuffle state."""
		if not self.songs:
			return -1

		shuffle_enabled = False
		# Use cached value to avoid async in sync context; setting is read on toggle.
		if hasattr(self, '_shuffle_on'):
			shuffle_enabled = self._shuffle_on

		if shuffle_enabled:
			if not self.shuffle_order or self.shuffle_index >= len(self.shuffle_order):
				self.shuffle_order = list(range(len(self.songs)))
				random.shuffle(self.shuffle_order)
				self.shuffle_index = 0
			idx = self.shuffle_order[self.shuffle_index]
			self.shuffle_index += 1
			return idx
		else:
			if self.current_song_index + 2 > len(self.songs):
				return 0
			return self.current_song_index + 1

	async def map_end(self, *args, **kwargs):
		# Reset skip votes on map change.
		self.skip_votes.clear()
		if self.skip_vote_task and not self.skip_vote_task.done():
			self.skip_vote_task.cancel()
			self.skip_vote_task = None

		# Ignore when no songs are added.
		if not self.songs:
			return

		if self.playlist:
			new_song = self.playlist[0]['song']
			self.playlist.pop(0)
		else:
			next_idx = self._get_next_song_index()
			if next_idx < 0:
				return
			new_song = self.songs[next_idx]
			self.current_song_index = next_idx

		try:
			override_map_music = await self.setting_override_map_music.get_value()
			await self.instance.gbx('SetForcedMusic', override_map_music, new_song[0])
			self.current_song = new_song
		except Exception as e:
			logger.error('[Music] Failed to set forced music: %s', e)

	async def _play_next_now(self):
		"""Force-play the next song immediately (used by skip)."""
		if not self.songs:
			return

		if self.playlist:
			new_song = self.playlist[0]['song']
			self.playlist.pop(0)
		else:
			next_idx = self._get_next_song_index()
			if next_idx < 0:
				return
			new_song = self.songs[next_idx]
			self.current_song_index = next_idx

		try:
			override_map_music = await self.setting_override_map_music.get_value()
			await self.instance.gbx('SetForcedMusic', override_map_music, new_song[0])
			self.current_song = new_song
			message = '$ff0Now playing: $fff{}$z$s$ff0 by $fff{}'.format(
				new_song[1].get('title', 'Unknown'), new_song[1].get('artist', 'Unknown'))
			await self.instance.chat(message)
		except Exception as e:
			logger.error('[Music] Failed to set forced music: %s', e)

	# ---- Shuffle ----

	async def toggle_shuffle(self, player, data, **kwargs):
		"""Toggle shuffle mode."""
		current = await self.setting_shuffle_enabled.get_value()
		new_value = not current
		await self.setting_shuffle_enabled.set_value(new_value)
		self._shuffle_on = new_value

		if new_value:
			# Reset shuffle order.
			self.shuffle_order = list(range(len(self.songs)))
			random.shuffle(self.shuffle_order)
			self.shuffle_index = 0
			message = '$ff0Shuffle mode $0f0enabled$ff0 by $fff{}$z$s$ff0.'.format(player.nickname)
		else:
			self.shuffle_order = []
			message = '$ff0Shuffle mode $f00disabled$ff0 by $fff{}$z$s$ff0.'.format(player.nickname)
		await self.instance.chat(message)

	# ---- Force next song ----

	async def force_next_song(self, player, data, **kwargs):
		"""Admin command to immediately switch to the next song mid-map.
		Forces a map restart so the game client reloads the music."""
		await self._play_next_now()
		# Force map restart so the client picks up the new music URL.
		try:
			await self.instance.gbx('RestartMap')
		except Exception as e:
			logger.warning('[Music] Could not restart map: %s', e)

	# ---- Vote-skip ----

	async def vote_skip(self, player, data, **kwargs):
		"""Vote to skip the current song."""
		if not self.current_song:
			await self.instance.chat('$i$f00No song is currently playing.', player)
			return

		skip_enabled = await self.setting_skip_vote_enabled.get_value()
		if not skip_enabled:
			await self.instance.chat('$i$f00Vote-skip is disabled.', player)
			return

		if player.login in self.skip_votes:
			await self.instance.chat('$i$f00You have already voted to skip.', player)
			return

		self.skip_votes.add(player.login)

		# Calculate threshold.
		threshold = await self.setting_skip_vote_threshold.get_value()
		online_players = self.instance.player_manager.online
		required = max(1, int(len(online_players) * threshold))
		current_votes = len(self.skip_votes)

		if current_votes >= required:
			# Skip succeeded.
			self.skip_votes.clear()
			if self.skip_vote_task and not self.skip_vote_task.done():
				self.skip_vote_task.cancel()
				self.skip_vote_task = None

			message = '$ff0Vote-skip passed! ($fff{}/{}$ff0 votes) Skipping...'.format(current_votes, required)
			await self.instance.chat(message)
			await self._play_next_now()
		else:
			message = '$ff0$fff{}$z$s$ff0 voted to skip. ($fff{}/{}$ff0 votes needed)'.format(
				player.nickname, current_votes, required)
			await self.instance.chat(message)

			# Start timeout if this is the first vote.
			if current_votes == 1:
				timeout = await self.setting_skip_vote_timeout.get_value()
				self.skip_vote_task = asyncio.ensure_future(self._skip_vote_timeout(timeout))

	async def _skip_vote_timeout(self, timeout):
		"""Cancel the skip vote after timeout seconds."""
		try:
			await asyncio.sleep(timeout)
			if self.skip_votes:
				self.skip_votes.clear()
				await self.instance.chat('$ff0Skip vote expired. Not enough votes.')
		except asyncio.CancelledError:
			pass

	# ---- Favorites ----

	async def add_favorite(self, player, data, **kwargs):
		"""Add the current song to player's favorites."""
		if not self.current_song:
			await self.instance.chat('$i$f00No song is currently playing.', player)
			return

		from .models import PlayerFavorite

		song_url, tags = self.current_song
		try:
			await PlayerFavorite.get_or_create(
				player=player.get_id(),
				song_url=song_url,
				defaults={
					'song_title': tags.get('title', 'Unknown'),
					'song_artist': tags.get('artist', 'Unknown'),
				}
			)
			message = '$ff0$fff{}$z$s$ff0 has been added to your favorites!'.format(tags.get('title', 'Unknown'))
		except Exception:
			message = '$i$f00This song is already in your favorites.'
		await self.instance.chat(message, player)

	async def remove_favorite(self, player, data, **kwargs):
		"""Remove the current song from player's favorites."""
		if not self.current_song:
			await self.instance.chat('$i$f00No song is currently playing.', player)
			return

		from .models import PlayerFavorite

		song_url, tags = self.current_song
		try:
			fav = await PlayerFavorite.get(
				PlayerFavorite.player == player.get_id(),
				PlayerFavorite.song_url == song_url,
			)
			await fav.destroy()
			message = '$ff0$fff{}$z$s$ff0 has been removed from your favorites.'.format(tags.get('title', 'Unknown'))
		except Exception:
			message = '$i$f00This song is not in your favorites.'
		await self.instance.chat(message, player)

	async def show_favorites(self, player, *args, **kwargs):
		"""Show the player's favorites list."""
		view = FavoritesListView(self, player)
		await view.display(player=player.login)

	async def show_top_favorites(self, player, data, **kwargs):
		"""Show server-wide most favorited songs."""
		from .models import PlayerFavorite
		from peewee import fn

		try:
			query = (PlayerFavorite
				.select(PlayerFavorite.song_url, PlayerFavorite.song_title, PlayerFavorite.song_artist, fn.COUNT(PlayerFavorite.id).alias('count'))
				.group_by(PlayerFavorite.song_url)
				.order_by(fn.COUNT(PlayerFavorite.id).desc())
				.limit(10))

			results = list(query)
			if not results:
				await self.instance.chat('$ff0No favorites yet!', player)
				return

			message = '$ff0Top Favorites:'
			await self.instance.chat(message, player)
			for i, row in enumerate(results):
				msg = '$ff0{}. $fff{}$z$s$ff0 by $fff{}$z$s$ff0 ({} favs)'.format(
					i + 1, row.song_title, row.song_artist, row.count)
				await self.instance.chat(msg, player)
		except Exception as e:
			logger.error('[Music] Failed to query top favorites: %s', e)
			await self.instance.chat('$i$f00Failed to load top favorites.', player)

	# ---- YouTube integration ----

	async def yt_play(self, player, data, **kwargs):
		"""Download YouTube audio and add to queue."""
		if not self.yt_available:
			await self.instance.chat('$i$f00YouTube integration is not available.', player)
			return

		url = str(data.url)

		await self.instance.chat(
			'$ff0Downloading audio from YouTube... Please wait.', player)

		try:
			from .youtube import download_audio

			yt_dlp_path = await self.setting_yt_dlp_path.get_value()
			ffmpeg_path = await self.setting_ffmpeg_path.get_value()
			download_dir = await self.setting_yt_download_dir.get_value()
			max_duration = await self.setting_yt_max_duration.get_value()
			max_filesize = await self.setting_yt_max_filesize.get_value()

			filepath, tags = await download_audio(
				url, download_dir, yt_dlp_path, ffmpeg_path, max_duration, max_filesize)

			filename = os.path.basename(filepath)

			# Determine the URL to use for SetForcedMusic.
			if self.http_server:
				song_url = self.http_server.get_public_url(filename)
			else:
				# Fall back to file path if no HTTP server.
				song_url = filepath

			new_song = (song_url, tags)
			self.songs.insert(self.current_song_index + 1, new_song)

			message = '$ff0YouTube: $fff{}$z$s$ff0 by $fff{}$z$s$ff0 added to songlist by $fff{}$z$s$ff0.'.format(
				tags.get('title', 'Unknown'), tags.get('artist', 'Unknown'), player.nickname)
			await self.instance.chat(message)
		except Exception as e:
			logger.error('[Music] YouTube download failed: %s', e)
			await self.instance.chat('$i$f00YouTube download failed: {}'.format(str(e)), player)

	# ---- Existing commands ----

	async def clear_playlist(self, player, data, **kwargs):
		async with self.lock:
			if len(self.playlist) > 0:
				self.playlist.clear()
				message = '$ff0Admin $fff{}$z$s$ff0 has cleared the playlist.'.format(player.nickname)
				await self.instance.chat(message)
			else:
				message = '$i$f00There are currently no songs in the playlist.'
				await self.instance.chat(message, player)

	async def play_song(self, player, data, *args, **kwargs):
		song = str(data.songname)
		try:
			async with aiohttp.ClientSession() as session:
				url, tags = (song, await self.get_tags(session, song))
				self.songs.insert(self.current_song_index + 1, (url, tags))
				message = '$fff{}$z$s$fa0 was added to the songlist by $fff{}$z$s$fa0.'\
					.format(tags['title'] + " - " + tags['artist'], player.nickname)
				await self.instance.chat(message)
		except Exception as e:
			await self.instance.chat(str(e), player)

	async def get_tags(self, session, url):
		tag_mapping = {
			'album': 'album',
			'albumartist': 'albumartist',
			'title': 'title',
			'artist': 'artist',
			'date': 'year',
			'tracknumber': 'track',
			'discnumber': 'disc',
			'genre': 'genre',
		}
		try:
			with async_timeout.timeout(10):
				async with session.get(url) as response:
					fs = await response.content.read()
					ogg = TinyTag.get(file_obj=BytesIO(fs))
					tags = {}
					for key, attr in tag_mapping.items():
						value = getattr(ogg, attr, None)
						tags[key] = value if value else 'Unknown'
					return tags
		except Exception as e:
			logger.warning('Failed to get tags for %s: %s', url, e)
			return {key: 'Unknown' for key in tag_mapping}

	async def get_songs(self):
		setting = settings.SONGS
		if isinstance(setting, dict) and self.instance.process_name in setting:
			setting = setting[self.instance.process_name]
		if not isinstance(setting, list):
			setting = None

		if not setting:
			message = '$ff0Default song setting not configured in your settings file!'
			await self.instance.chat(message)
			return []

		self.songs.clear()
		songlist = setting
		async with aiohttp.ClientSession() as session:
			tag_list = await asyncio.gather(
				*[self.get_tags(session, song) for song in songlist],
				return_exceptions=True
			)

		results = []
		for i, song in enumerate(songlist):
			tags = tag_list[i]
			if isinstance(tags, Exception):
				logger.warning('Failed to load metadata for %s: %s', song, tags)
				tags = {k: 'Unknown' for k in ('album', 'albumartist', 'title', 'artist', 'date', 'tracknumber', 'discnumber', 'genre')}
			results.append((song.replace("%20", " "), tags))
		return results

	async def get_current_song(self, player, *args, **kwargs):
		if self.current_song:
			song_url, tags = self.current_song
			shuffle_on = getattr(self, '_shuffle_on', False)
			shuffle_str = ' $0f0[Shuffle]' if shuffle_on else ''
			message = '$ff0The current song is $fff{}$z$s$ff0 by $fff{}{}'.format(
				tags['title'], tags['artist'], shuffle_str)
		else:
			message = '$i$f00There is no current song. Skip or restart!'
		await self.instance.chat(message, player)
