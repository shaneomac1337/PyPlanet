import asyncio
import json
import logging
import os
import shutil

from tinytag import TinyTag

logger = logging.getLogger(__name__)


async def check_binary(path):
	"""Check if a binary exists and is executable."""
	for flag in ['-version', '--version']:
		try:
			proc = await asyncio.create_subprocess_exec(
				path, flag,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
			)
			await asyncio.wait_for(proc.communicate(), timeout=5)
			if proc.returncode == 0:
				return True
		except Exception:
			continue
	return False


async def fetch_yt_metadata(url, yt_dlp_path='yt-dlp'):
	"""Fetch metadata from a YouTube URL using yt-dlp --dump-json (no download)."""
	try:
		proc = await asyncio.create_subprocess_exec(
			yt_dlp_path,
			'--dump-json',
			'--no-playlist',
			'--no-warnings',
			url,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE,
		)
		stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
		if proc.returncode != 0:
			return None

		info = json.loads(stdout.decode())
		return {
			'title': info.get('title') or info.get('fulltitle') or 'Unknown',
			'artist': info.get('artist') or info.get('creator') or info.get('uploader') or info.get('channel') or 'Unknown',
			'album': info.get('album') or 'YouTube',
			'albumartist': info.get('album_artist') or info.get('channel') or 'Unknown',
			'date': str(info.get('release_year') or info.get('upload_date', '')[:4] or 'Unknown'),
			'genre': info.get('genre') or 'Unknown',
			'tracknumber': str(info.get('track_number') or 'Unknown'),
			'discnumber': 'Unknown',
		}
	except Exception as e:
		logger.warning('Failed to fetch YouTube metadata for %s: %s', url, e)
		return None


async def download_audio(url, output_dir, yt_dlp_path='yt-dlp', ffmpeg_path='ffmpeg',
						 max_duration=600, max_filesize='50M'):
	"""Download audio from a URL using yt-dlp and convert to .ogg vorbis.

	Returns (filepath, tags_dict) on success, raises on failure.
	"""
	os.makedirs(output_dir, exist_ok=True)

	# Fetch metadata from yt-dlp before downloading (fast, JSON only).
	yt_tags = await fetch_yt_metadata(url, yt_dlp_path)

	# Resolve ffmpeg to absolute path (standalone yt-dlp can't find it on PATH).
	resolved_ffmpeg = shutil.which(ffmpeg_path) or ffmpeg_path

	# Download and extract audio, convert to vorbis ogg.
	output_template = os.path.join(output_dir, '%(id)s.%(ext)s')
	cmd = [
		yt_dlp_path,
		'--no-playlist',
		'--extract-audio',
		'--audio-format', 'vorbis',
		'--audio-quality', '5',
		'--max-filesize', str(max_filesize),
		'--match-filter', 'duration <= {}'.format(max_duration),
		'--ffmpeg-location', resolved_ffmpeg,
		'--output', output_template,
		'--no-embed-thumbnail',
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

	# Use yt-dlp metadata if available, fall back to file tags.
	if yt_tags and yt_tags.get('title') != 'Unknown':
		tags = yt_tags
		# Write the metadata into the .ogg file so it persists.
		await write_ogg_tags(filepath, tags, resolved_ffmpeg)
	else:
		tags = get_file_tags(filepath)

	return filepath, tags


async def write_ogg_tags(filepath, tags, ffmpeg_path='ffmpeg'):
	"""Write metadata tags into an .ogg file using ffmpeg."""
	temp_path = filepath + '.tmp.ogg'
	cmd = [
		ffmpeg_path, '-i', filepath, '-vn', '-c:a', 'copy',
		'-metadata', 'title={}'.format(tags.get('title', '')),
		'-metadata', 'artist={}'.format(tags.get('artist', '')),
		'-metadata', 'album={}'.format(tags.get('album', '')),
		'-metadata', 'date={}'.format(tags.get('date', '')),
		'-metadata', 'genre={}'.format(tags.get('genre', '')),
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
	except Exception as e:
		logger.warning('Failed to write tags to %s: %s', filepath, e)
		if os.path.exists(temp_path):
			os.remove(temp_path)


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
