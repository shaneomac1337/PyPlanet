from peewee import *

from pyplanet.core.db import TimedModel
from pyplanet.apps.core.maniaplanet.models import Player


class PlayerFavorite(TimedModel):
	player = ForeignKeyField(Player, index=True)
	song_url = CharField(max_length=512)
	song_title = CharField(default='Unknown')
	song_artist = CharField(default='Unknown')

	class Meta:
		indexes = (
			(('player', 'song_url'), True),
		)
