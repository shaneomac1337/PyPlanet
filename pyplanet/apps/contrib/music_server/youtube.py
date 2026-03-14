import asyncio
import logging
import os

from tinytag import TinyTag
from io import BytesIO

logger = logging.getLogger(__name__)


async def check_binary(path):
	"""Check if a binary exists and is executable."""
	try:
		proc = await asyncio.create_subprocess_exec(
			path, '--version',
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		await asyncio.wait_for(proc.communicate(), timeout=5)
		return proc.returncode == 0
	except Exception:
		return False


async def download_audio(url, output_dir, yt_dlp_path='yt-dlp', ffmpeg_path='ffmpeg',
						 max_duration=600, max_filesize='50M'):
	"""Download audio from a URL using yt-dlp and convert to .ogg vorbis.

	Returns (filepath, tags_dict) on success, raises on failure.
	"""
	os.makedirs(output_dir, exist_ok=True)

	# Use yt-dlp to download and extract audio, let ffmpeg convert to vorbis ogg.
	output_template = os.path.join(output_dir, '%(id)s.%(ext)s')
	cmd = [
		yt_dlp_path,
		'--no-playlist',
		'--extract-audio',
		'--audio-format', 'vorbis',
		'--audio-quality', '5',
		'--max-filesize', max_filesize,
		'--match-filter', 'duration <= {}'.format(max_duration),
		'--ffmpeg-location', ffmpeg_path,
		'--output', output_template,
		'--no-warnings',
		'--quiet',
		url,
	]

	proc = await asyncio.create_subprocess_exec(
		*cmd,
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.PIPE,
	)
	stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

	if proc.returncode != 0:
		error_msg = stderr.decode().strip() if stderr else 'Unknown error'
		raise RuntimeError('yt-dlp failed: {}'.format(error_msg))

	# Find the output file (yt-dlp uses video ID as filename).
	ogg_files = [f for f in os.listdir(output_dir) if f.endswith('.ogg')]
	if not ogg_files:
		raise RuntimeError('No .ogg file found after download')

	# Get the most recently modified .ogg file.
	ogg_files.sort(key=lambda f: os.path.getmtime(os.path.join(output_dir, f)), reverse=True)
	filepath = os.path.join(output_dir, ogg_files[0])

	# Extract metadata.
	tags = get_file_tags(filepath)

	return filepath, tags


def get_file_tags(filepath):
	"""Extract metadata tags from a local audio file."""
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
		ogg = TinyTag.get(filepath)
		tags = {}
		for key, attr in tag_mapping.items():
			value = getattr(ogg, attr, None)
			tags[key] = value if value else 'Unknown'
		return tags
	except Exception as e:
		logger.warning('Failed to read tags from %s: %s', filepath, e)
		return {key: 'Unknown' for key in tag_mapping}


def cleanup_old_files(directory, max_age_days):
	"""Remove .ogg files older than max_age_days from directory."""
	if max_age_days <= 0 or not os.path.isdir(directory):
		return

	import time
	now = time.time()
	cutoff = now - (max_age_days * 86400)
	removed = 0

	for filename in os.listdir(directory):
		if not filename.endswith('.ogg'):
			continue
		filepath = os.path.join(directory, filename)
		try:
			if os.path.getmtime(filepath) < cutoff:
				os.remove(filepath)
				removed += 1
		except OSError:
			pass

	if removed:
		logger.info('Cleaned up %d old music files from %s', removed, directory)
