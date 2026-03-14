from pyplanet.views.generics.list import ManualListView
from pyplanet.views.generics.alert import ask_input, ask_confirmation


class MusicListView(ManualListView):
	title = 'Songs'
	icon_style = 'Icons128x128_1'
	icon_substyle = 'Statistics'

	def __init__(self, app):
		super().__init__(self)
		self.app = app
		self.manager = app.context.ui
		self.provide_search = True

	async def get_fields(self):
		return [
			{
				'name': '#',
				'index': 'index',
				'sorting': True,
				'searching': False,
				'width': 10,
				'type': 'label'
			},
			{
				'name': 'Song',
				'index': 'song_name',
				'sorting': True,
				'searching': True,
				'width': 80,
				'action': self.action_playlist
			},
			{
				'name': 'Artist',
				'index': 'song_artist',
				'sorting': True,
				'searching': True,
				'width': 70,
			},
			{
				'name': 'Favs',
				'index': 'fav_count',
				'sorting': True,
				'searching': False,
				'width': 15,
				'type': 'label'
			},
			{
				'name': '',
				'index': 'now_playing',
				'sorting': False,
				'searching': False,
				'width': 15,
				'type': 'label'
			},
		]

	async def get_data(self):
		items = []
		song_list = self.app.songs
		current_song = self.app.current_song

		# Get favorite counts if favorites are enabled.
		fav_counts = {}
		try:
			from .models import PlayerFavorite
			from peewee import fn
			query = (PlayerFavorite
				.select(PlayerFavorite.song_url, fn.COUNT(PlayerFavorite.id).alias('count'))
				.group_by(PlayerFavorite.song_url))
			for row in query:
				fav_counts[row.song_url] = row.count
		except Exception:
			pass

		for song in song_list:
			tags = song[1]
			is_playing = current_song and current_song[0] == song[0]
			items.append({
				'index': song_list.index(song) + 1,
				'song_name': tags.get('title', '-unknown title-'),
				'song_artist': tags.get('artist', '-unknown artist-'),
				'fav_count': fav_counts.get(song[0], 0),
				'now_playing': '$0f0Playing' if is_playing else '',
			})
		return items

	async def action_playlist(self, player, values, song_info, *args, **kwargs):
		await self.app.add_to_playlist(player, song_info['index'] - 1)


class PlaylistView(ManualListView):
	title = 'Playlist'
	icon_style = 'Icons128x128_1'
	icon_substyle = 'Statistics'

	def __init__(self, app):
		super().__init__(self)
		self.app = app
		self.manager = app.context.ui
		self.provide_search = True

	async def get_fields(self):
		return [
			{
				'name': '#',
				'index': 'index',
				'sorting': True,
				'searching': False,
				'width': 10,
				'type': 'label'
			},
			{
				'name': 'Song',
				'index': 'song_name',
				'sorting': True,
				'searching': True,
				'width': 80,
				'action': self.action_drop
			},
			{
				'name': 'Artist',
				'index': 'song_artist',
				'sorting': True,
				'searching': True,
				'width': 50,
			},
			{
				'name': 'Requested by',
				'index': 'juke_player',
				'sorting': True,
				'searching': True,
				'width': 50,
			},
			{
				'name': 'Source',
				'index': 'source',
				'sorting': True,
				'searching': False,
				'width': 20,
				'type': 'label'
			},
		]

	async def action_drop(self, player, values, song_info, **kwargs):
		await self.app.drop_from_playlist(player, song_info)

	async def get_data(self):
		items = []
		playlist = self.app.playlist
		for song in playlist:
			tags = song['song'][1]
			song_url = song['song'][0]
			player = song['player']

			# Determine source: if served by our HTTP server, it's a YT download.
			source = 'Config'
			if self.app.http_server and self.app.http_server.public_url and song_url.startswith(self.app.http_server.public_url):
				source = 'YT'

			items.append({
				'index': playlist.index(song) + 1,
				'song_name': tags.get('title', '-unknown title-'),
				'song_artist': tags.get('artist', '-unknown artist-'),
				'juke_player': player.nickname if hasattr(player, 'nickname') else str(player),
				'source': source,
			})
		return items


class FavoritesListView(ManualListView):
	title = 'My Favorites'
	icon_style = 'Icons128x128_1'
	icon_substyle = 'Statistics'

	def __init__(self, app, player):
		super().__init__(self)
		self.app = app
		self.player = player
		self.manager = app.context.ui
		self.provide_search = True

	async def get_fields(self):
		return [
			{
				'name': '#',
				'index': 'index',
				'sorting': True,
				'searching': False,
				'width': 10,
				'type': 'label'
			},
			{
				'name': 'Song',
				'index': 'song_name',
				'sorting': True,
				'searching': True,
				'width': 100,
				'action': self.action_queue
			},
			{
				'name': 'Artist',
				'index': 'song_artist',
				'sorting': True,
				'searching': True,
				'width': 100,
			},
		]

	async def get_data(self):
		items = []
		try:
			from .models import PlayerFavorite
			favs = list(PlayerFavorite.select().where(
				PlayerFavorite.player == self.player.get_id()
			).order_by(PlayerFavorite.created_at.desc()))

			for i, fav in enumerate(favs):
				items.append({
					'index': i + 1,
					'song_name': fav.song_title,
					'song_artist': fav.song_artist,
					'song_url': fav.song_url,
				})
		except Exception:
			pass
		return items

	async def action_queue(self, player, values, song_info, *args, **kwargs):
		"""Queue a favorite song."""
		# Find the song in the current songs list by URL.
		song_url = song_info.get('song_url', '')
		matching = [s for s in self.app.songs if s[0] == song_url]
		if matching:
			idx = self.app.songs.index(matching[0])
			await self.app.add_to_playlist(player, idx)
		else:
			await self.app.instance.chat(
				'$i$f00This song is not in the current songlist. It may have been removed.', player)


class SongManagerView(ManualListView):
	title = 'Song Manager'
	icon_style = 'Icons128x128_1'
	icon_substyle = 'Statistics'

	def __init__(self, app):
		super().__init__(self)
		self.app = app
		self.manager = app.context.ui
		self.provide_search = True

	async def get_fields(self):
		return [
			{
				'name': '#',
				'index': 'index',
				'sorting': True,
				'searching': False,
				'width': 10,
				'type': 'label'
			},
			{
				'name': 'Title',
				'index': 'song_name',
				'sorting': True,
				'searching': True,
				'width': 70,
				'action': self.action_edit_title
			},
			{
				'name': 'Artist',
				'index': 'song_artist',
				'sorting': True,
				'searching': True,
				'width': 50,
				'action': self.action_edit_artist
			},
			{
				'name': '',
				'index': 'now_playing',
				'sorting': False,
				'searching': False,
				'width': 15,
				'type': 'label'
			},
			{
				'name': 'Del',
				'index': 'delete_btn',
				'sorting': False,
				'searching': False,
				'width': 15,
				'action': self.action_delete,
				'type': 'label'
			},
		]

	async def get_data(self):
		items = []
		song_list = self.app.songs
		current_song = self.app.current_song
		for i, song in enumerate(song_list):
			tags = song[1]
			is_playing = current_song and current_song[0] == song[0]
			items.append({
				'index': i + 1,
				'song_name': tags.get('title', '-unknown-'),
				'song_artist': tags.get('artist', '-unknown-'),
				'song_url': song[0],
				'now_playing': '$0f0Playing' if is_playing else '',
				'delete_btn': '$f00X',
			})
		return items

	async def action_edit_title(self, player, values, song_info, *args, **kwargs):
		"""Prompt to edit song title."""
		current_title = song_info.get('song_name', '')
		new_title = await ask_input(
			player,
			'Edit song title:',
			size='md',
			default=current_title,
		)
		if new_title and new_title != current_title:
			await self.app.update_song_tag(song_info['song_url'], 'title', new_title)
			await self.app.instance.chat(
				'$ff0Title updated to: $fff{}'.format(new_title), player)
			await self.refresh(player)

	async def action_edit_artist(self, player, values, song_info, *args, **kwargs):
		"""Prompt to edit song artist."""
		current_artist = song_info.get('song_artist', '')
		new_artist = await ask_input(
			player,
			'Edit song artist:',
			size='md',
			default=current_artist,
		)
		if new_artist and new_artist != current_artist:
			await self.app.update_song_tag(song_info['song_url'], 'artist', new_artist)
			await self.app.instance.chat(
				'$ff0Artist updated to: $fff{}'.format(new_artist), player)
			await self.refresh(player)

	async def action_delete(self, player, values, song_info, *args, **kwargs):
		"""Confirm and delete a song."""
		title = song_info.get('song_name', 'this song')
		confirmed = await ask_confirmation(
			player,
			'Delete "$fff{}$z$s" from the song library?'.format(title),
			size='sm',
		)
		if confirmed:
			await self.app.remove_song(song_info['song_url'])
			await self.app.instance.chat(
				'$ff0Removed: $fff{}'.format(title), player)
			await self.refresh(player)
