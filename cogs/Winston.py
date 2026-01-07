import re, json, discord, asyncio, os, base64, time
from abc import ABC, abstractmethod
from utils.CogModule import CogModule
from discord.ext import commands, tasks
from urllib.parse import urlparse, parse_qs

from utils.STT import STTClient
import utils.utils as utils

YOUTUBE_REGEX = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.+$")


class MediaSourceStrategy(ABC):
	id: str

	@abstractmethod
	def can_handle(self, url: str) -> bool:
		...

	@abstractmethod
	def create_job(self, interaction: discord.Interaction, url: str) -> "TranscriptionJob | None":
		...

	@abstractmethod
	def build_ytdlp_cmd(self, job: "TranscriptionJob", audio_path: str) -> list[str]:
		...


class TranscriptionJob:
	def __init__(
		self,
		interaction: discord.Interaction,
		source: "MediaSourceStrategy",
		media_id: str,
		canonical_url: str,
		thumbnail_url: str | None = None,
	):
		self.interaction = interaction
		self.source = source
		self.media_id = media_id
		self.canonical_url = canonical_url
		self.thumbnail_url = thumbnail_url


class YouTubeSource(MediaSourceStrategy):
	id = "youtube"

	def can_handle(self, url: str) -> bool:
		return bool(YOUTUBE_REGEX.match(url))

	def _extract_video_id(self, url: str) -> str | None:
		parsed = urlparse(url)
		if parsed.netloc.endswith("youtu.be"):
			return parsed.path.strip("/")
		if "youtube.com" in parsed.netloc:
			query = parse_qs(parsed.query)
			if "v" in query:
				return query["v"][0]
			shorts_match = re.search(r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})", url)
			if shorts_match:
				return shorts_match.group(1)
		match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
		return match.group(1) if match else None

	def create_job(self, interaction: discord.Interaction, url: str) -> TranscriptionJob | None:
		video_id = self._extract_video_id(url)
		if not video_id:
			return None

		clean_url = f"https://www.youtube.com/watch?v={video_id}"
		thumb = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
		return TranscriptionJob(
			interaction=interaction,
			source=self,
			media_id=video_id,
			canonical_url=clean_url,
			thumbnail_url=thumb,
		)

	def build_ytdlp_cmd(self, job: TranscriptionJob, audio_path: str) -> list[str]:
		ytdlp_path = os.path.join(os.getcwd(), "yt-dlp.exe")
		return [
			ytdlp_path,
			"-f", "bestaudio",
			"--extract-audio",
			"--audio-format", "mp3",
			"-o", audio_path,
			job.canonical_url,
		]


class RedditSource(MediaSourceStrategy):
	id = "reddit"

	def can_handle(self, url: str) -> bool:
		parsed = urlparse(url)
		host = parsed.netloc.lower()
		return host.endswith("reddit.com") or host.endswith("v.redd.it")

	def _extract_media_id(self, url: str) -> str:
		parsed = urlparse(url)
		path = parsed.path or ""
		parts = [p for p in path.split("/") if p]

		# v.redd.it/<id>
		if parsed.netloc.lower().endswith("v.redd.it") and parts:
			return f"reddit_{parts[0]}"

		# reddit.com/r/<sub>/comments/<post_id>/...
		if "comments" in parts:
			idx = parts.index("comments")
			if idx + 1 < len(parts):
				return f"reddit_{parts[idx + 1]}"

		# Fallback: sanitised path
		sanitised = re.sub(r"\W+", "_", path.strip("/")) or "reddit"
		return f"reddit_{sanitised}"

	def create_job(self, interaction: discord.Interaction, url: str) -> TranscriptionJob | None:
		media_id = self._extract_media_id(url)
		canonical_url = url  # keep as provided; yt-dlp can handle it directly
		thumbnail_url = None  # could be enhanced later via metadata probe
		return TranscriptionJob(
			interaction=interaction,
			source=self,
			media_id=media_id,
			canonical_url=canonical_url,
			thumbnail_url=thumbnail_url,
		)

	def build_ytdlp_cmd(self, job: TranscriptionJob, audio_path: str) -> list[str]:
		ytdlp_path = os.path.join(os.getcwd(), "yt-dlp.exe")
		return [
			ytdlp_path,
			"-f", "bestaudio",
			"--extract-audio",
			"--audio-format", "mp3",
			"-o", audio_path,
			job.canonical_url,
		]


class WinstonCog(CogModule):
	"""Main winston orchestrator"""
	def __init__(self, bot: commands.Bot):
		super().__init__(bot)
		self.bot = bot

		# Supported media sources (strategies)
		self.sources: list[MediaSourceStrategy] = [
			YouTubeSource(),
			RedditSource(),
		]

		# Job management
		self.queue = asyncio.Queue()
		self.pending_jobs: list[TranscriptionJob] = []
		self.active_jobs: list[TranscriptionJob] = []

		# STT client lifecycle (lazy startup + warmup + idle shutdown)
		self.stt: STTClient | None = None
		self._stt_lock = asyncio.Lock()
		self._stt_last_used: float | None = None
		self._stt_busy: bool = False
		self._stt_idle_timeout = 5 * 60      # 5 minutes
		self._stt_warmup_seconds = 5         # backend warm-up

		# Static STT configuration
		self._stt_host = "127.0.0.1"
		self._stt_endpoint = "/inference"
		self._stt_config = {
			"model": "./models/whisper-large-v3-turbo-Q8_0.bin",
			"vad": "./models/vad-silero-v5.1.2.bin",
			"prompt": "A conversation with Emma, Usi, Vedal and Neuro-sama:",
			"hyperparameters": {
				"beam_size": 8
			}
		}
		self._stt_log_dir = "./logs/subprocesses"

		# Background workers
		self.worker.start()
		self.stt_idle_task.start()

	def build_embed(self, title: str, color: discord.Color, builder_fn=None):
		"""
		Creates a base embed with Winston branding.
		builder_fn(embed) can be provided to customise contents.
		"""
		avatar = self.bot.user.display_avatar.url if self.bot.user else None
		embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
		embed.set_author(name="Winston", icon_url=avatar)
		embed.set_footer(text="Winston v0.0.1a", icon_url=avatar)

		if callable(builder_fn):
			builder_fn(embed)

		return embed

	async def _ensure_stt_running(self):
		"""
		Lazily start the STT server if it's not running yet.
		Waits a short warmup so the backend is ready to accept requests.
		"""
		async with self._stt_lock:
			if self.stt is not None:
				return

			loop = asyncio.get_running_loop()

			def create_client():
				port = utils.get_free_port()
				return STTClient(
					self._stt_host,
					port,
					self._stt_endpoint,
					self._stt_config,
					self._stt_log_dir
				)

			# Run potentially blocking process spawn in a thread pool
			self.stt = await loop.run_in_executor(None, create_client)
			print("STT client started")

		# Warm-up period so the backend process is actually ready
		await asyncio.sleep(self._stt_warmup_seconds)
		self._stt_last_used = time.perf_counter()

	@discord.app_commands.command(name="transcribe", description="Transcribe a video link (YouTube, Reddit)")
	@discord.app_commands.describe(link="The video URL")
	async def transcribe(self, interaction: discord.Interaction, link: str):
		source: MediaSourceStrategy | None = None
		for s in self.sources:
			if s.can_handle(link):
				source = s
				break

		if not source:
			embed = self.build_embed(
				"❌ Unsupported Link",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="That link doesn't look like a supported video source.\n"
					      "Currently supported: **YouTube**, **Reddit (v.redd.it / reddit.com)**."
				)
			)
			await interaction.response.send_message(embed=embed, ephemeral=False)
			return

		job = source.create_job(interaction, link)
		if not job:
			embed = self.build_embed(
				"❌ Couldn’t Prepare Media",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="The link seems malformed or missing required media information."
				)
			)
			await interaction.response.send_message(embed=embed, ephemeral=False)
			return

		embed = self.build_embed(
			"🎙️ Transcribing...",
			discord.Color.blurple(),
			lambda e: [
				e.set_thumbnail(url=job.thumbnail_url) if job.thumbnail_url else None,
				e.add_field(name="Source", value=f"[Click to open source]({job.canonical_url})", inline=False)
			]
		)

		await interaction.response.send_message(embed=embed, ephemeral=False)

		self.pending_jobs.append(job)
		await self.queue.put(job)

	@discord.app_commands.command(name="queue", description="View current transcription job queue")
	async def view_queue(self, interaction: discord.Interaction):
		pending = len(self.pending_jobs)
		active = len(self.active_jobs)

		if pending == 0 and active == 0:
			embed = self.build_embed(
				"📭 Transcription Queue Empty",
				discord.Color.greyple(),
				lambda e: e.add_field(
					name="Status",
					value="There are currently **no pending or active transcription jobs**.",
					inline=False
				)
			)
			await interaction.response.send_message(embed=embed, ephemeral=False)
			return

		def format_jobs(title, jobs):
			if not jobs:
				return f"**No {title.lower()} jobs.**"
			lines = []
			for i, job in enumerate(jobs, 1):
				lines.append(f"**{i}.** [Source]({job.canonical_url}) • {job.interaction.user.mention}")
			return "\n".join(lines)

		embed = self.build_embed(
			"📋 Transcription Queue",
			discord.Color.orange(),
			lambda e: [
				e.add_field(name=f"🕓 Active Jobs ({active})", value=format_jobs("Active", self.active_jobs), inline=False),
				e.add_field(name=f"⏳ Pending Jobs ({pending})", value=format_jobs("Pending", self.pending_jobs), inline=False)
			]
		)

		await interaction.response.send_message(embed=embed, ephemeral=False)

	@tasks.loop(seconds=1)
	async def worker(self):
		if self.queue.empty():
			return

		job: TranscriptionJob = await self.queue.get()
		if job in self.pending_jobs:
			self.pending_jobs.remove(job)
		self.active_jobs.append(job)

		start_time = time.perf_counter()  # ⏱️ start tracking
		print(f"Starting transcription for {job.canonical_url}")

		# 🧱 Step 1: Download audio using yt_dlp via strategy
		os.makedirs("downloads", exist_ok=True)
		audio_path = os.path.join("downloads", f"{job.media_id}.mp3")

		ytdlp_cmd = job.source.build_ytdlp_cmd(job, audio_path)

		process = await asyncio.create_subprocess_exec(
			*ytdlp_cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		stdout, stderr = await process.communicate()

		if process.returncode != 0:
			print(f"yt_dlp failed:\n{stderr.decode()}")
			embed = self.build_embed(
				"❌ Download Failed",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="Could not download audio. The media might be private or blocked."
				)
			)
			await job.interaction.channel.send(embed=embed)
			self.active_jobs.remove(job)
			return

		print(f"Download complete → {audio_path}")

		# 🎧 Step 2: Convert to mono 16 kHz WAV (Whisper-friendly)
		wav_path = os.path.join("downloads", f"{job.media_id}_16k.wav")

		ffmpeg_path = "ffmpeg"  # assumes ffmpeg.exe is in PATH
		ffmpeg_cmd = [
			ffmpeg_path, "-y",
			"-i", audio_path,
			"-ac", "1",            # mono
			"-ar", "16000",        # 16 kHz
			"-f", "wav",
			wav_path
		]

		ffmpeg_proc = await asyncio.create_subprocess_exec(
			*ffmpeg_cmd,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE
		)
		stdout, stderr = await ffmpeg_proc.communicate()

		if ffmpeg_proc.returncode != 0:
			print(f"ffmpeg conversion failed:\n{stderr.decode()}")
			embed = self.build_embed(
				"❌ Audio Conversion Failed",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="Failed to convert audio to mono 16 kHz WAV for Whisper."
				)
			)
			await job.interaction.channel.send(embed=embed)
			self.active_jobs.remove(job)
			return

		print(f"Converted → {wav_path}")

		# Ensure STT backend is up before we send audio
		await self._ensure_stt_running()

		# 🧬 Step 3: Encode to base64 for Whisper
		with open(wav_path, "rb") as f:
			audio_b64 = base64.b64encode(f.read()).decode()

		# 🎙️ Step 4: Transcribe
		try:
			self._stt_busy = True
			if not self.stt:
				raise RuntimeError("STT client not initialised")
			transcript = self.stt.transcribe(audio_b64)
		except Exception as e:
			print(f"Transcription failed: {e}")
			embed = self.build_embed(
				"❌ Transcription Failed",
				discord.Color.red(),
				lambda e: e.add_field(name="Error", value=f"```\n{e}\n```")
			)
			await job.interaction.channel.send(embed=embed)
			self.active_jobs.remove(job)
			return
		finally:
			self._stt_busy = False
			self._stt_last_used = time.perf_counter()

		# 🕒 Step 5: Compute total time taken
		elapsed = time.perf_counter() - start_time
		mins, secs = divmod(int(elapsed), 60)
		elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

		# 🗒️ Step 6: Save transcript
		os.makedirs("transcripts", exist_ok=True)
		file_path = f"./transcripts/{job.media_id}.txt"
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(transcript)

		# ✅ Step 7: Send result
		embed = self.build_embed(
			title="✅ Transcription Complete",
			color=discord.Color.green(),
			builder_fn=lambda e: [
				e.add_field(name="Source", value=f"[Open source]({job.canonical_url})", inline=False),
				e.add_field(name="Time Taken", value=elapsed_str, inline=True),
				e.set_thumbnail(url=job.thumbnail_url) if job.thumbnail_url else None
			]
		)

		if len(transcript) <= 1000:
			embed.add_field(name="Transcript", value=transcript, inline=False)
			await job.interaction.channel.send(
				content=f"{job.interaction.user.mention} The transcript is ready.",
				embed=embed
			)
		else:
			file = discord.File(file_path, filename=f"{job.media_id}.txt")
			await job.interaction.channel.send(
				content=f"{job.interaction.user.mention} Here's the transcript file.",
				embed=embed,
				file=file
			)

		self.active_jobs.remove(job)
		print(f"Finished transcription for {job.canonical_url} in {elapsed_str}")

	@tasks.loop(seconds=30)
	async def stt_idle_task(self):
		"""
		Periodically check if the STT server has been idle long enough
		and shut it down if so.
		"""
		if self.stt is None:
			return
		if self._stt_busy:
			return
		if self._stt_last_used is None:
			return

		now = time.perf_counter()
		if now - self._stt_last_used < self._stt_idle_timeout:
			return

		async with self._stt_lock:
			# Re-check inside the lock for safety
			if self.stt is None or self._stt_busy:
				return

			print("Shutting down STT server due to inactivity...")
			self.stt.close()
			self.stt = None
			self._stt_last_used = None

	def cog_unload(self):
		self.worker.cancel()
		self.stt_idle_task.cancel()
		if self.stt is not None:
			self.stt.close()


async def setup(bot: commands.Bot):
	await bot.add_cog(WinstonCog(bot))