import re, json, discord, asyncio, os, base64, time
from utils.CogModule import CogModule
from discord.ext import commands, tasks
from urllib.parse import urlparse, parse_qs

from utils.STT import STTClient
import utils.utils as utils

YOUTUBE_REGEX = re.compile(r"^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.+$")

class TranscriptionJob:
	def __init__(self, interaction: discord.Interaction, video_id: str, clean_url: str):
		self.interaction = interaction
		self.video_id = video_id
		self.clean_url = clean_url

class WinstonCog(CogModule):
	"""Main winston orchestrator"""
	def __init__(self, bot: commands.Bot):
		super().__init__(bot)
		self.bot = bot

		self.queue = asyncio.Queue()
		self.pending_jobs = []
		self.active_jobs = []
		self.worker.start()

		self.stt = STTClient("127.0.0.1", utils.get_free_port(), "/inference", {
			"model": "./models/whisper-large-v3-turbo-Q8_0.bin",
			"vad": "./models/vad-silero-v5.1.2.bin",
			"prompt": "A conversation with Emma, Usi, Vedal and Neuro-sama:",
			"hyperparameters": {
				"beam_size": 8
			}
		}, "./logs/subprocesses")

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

	@discord.app_commands.command(name="transcribe", description="Transcribe a YouTube video link")
	@discord.app_commands.describe(link="The YouTube video URL")
	async def transcribe(self, interaction: discord.Interaction, link: str):
		if not YOUTUBE_REGEX.match(link):
			embed = self.build_embed(
				"❌ Invalid YouTube Link",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="That doesn’t look like a valid YouTube video link.\nPlease provide a proper `youtube.com` or `youtu.be` URL."
				)
			)
			await interaction.response.send_message(embed=embed, ephemeral=False)
			return

		video_id = self.extract_video_id(link)
		if not video_id:
			embed = self.build_embed(
				"❌ Couldn’t Extract Video ID",
				discord.Color.red(),
				lambda e: e.add_field(
					name="Error",
					value="That YouTube link seems malformed or missing a video ID."
				)
			)
			await interaction.response.send_message(embed=embed, ephemeral=False)
			return

		clean_url = f"https://www.youtube.com/watch?v={video_id}"

		embed = self.build_embed(
			"🎙️ Transcribing...",
			discord.Color.blurple(),
			lambda e: [
				e.set_thumbnail(url=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"),
				e.add_field(name="Source", value=f"[Click to open video]({clean_url})", inline=False)
			]
		)

		await interaction.response.send_message(embed=embed, ephemeral=False)

		job = TranscriptionJob(interaction, video_id, clean_url)
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
				lines.append(f"**{i}.** [Video]({job.clean_url}) • {job.interaction.user.mention}")
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


	@staticmethod
	def extract_video_id(url: str) -> str | None:
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


	@tasks.loop(seconds=1)
	async def worker(self):
		if self.queue.empty():
			return

		job = await self.queue.get()
		if job in self.pending_jobs:
			self.pending_jobs.remove(job)
		self.active_jobs.append(job)

		start_time = time.perf_counter()  # ⏱️ start tracking
		print(f"Starting transcription for {job.clean_url}")

		# 🧱 Step 1: Download audio using yt_dlp
		os.makedirs("downloads", exist_ok=True)
		audio_path = os.path.join("downloads", f"{job.video_id}.mp3")

		ytdlp_path = os.path.join(os.getcwd(), "yt-dlp.exe")
		ytdlp_cmd = [
			ytdlp_path,
			"-f", "bestaudio",
			"--extract-audio",
			"--audio-format", "mp3",
			"-o", audio_path,
			job.clean_url
		]

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
					value="Could not download audio. The video might be private or blocked."
				)
			)
			await job.interaction.channel.send(embed=embed)
			self.active_jobs.remove(job)
			return

		print(f"Download complete → {audio_path}")

		# 🎧 Step 2: Convert to mono 16 kHz WAV (Whisper-friendly)
		wav_path = os.path.join("downloads", f"{job.video_id}_16k.wav")

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

		# 🧬 Step 3: Encode to base64 for Whisper
		with open(wav_path, "rb") as f:
			audio_b64 = base64.b64encode(f.read()).decode()

		# 🎙️ Step 4: Transcribe
		try:
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

		# 🕒 Step 5: Compute total time taken
		elapsed = time.perf_counter() - start_time
		mins, secs = divmod(int(elapsed), 60)
		elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

		# 🗒️ Step 6: Save transcript
		os.makedirs("transcripts", exist_ok=True)
		file_path = f"./transcripts/{job.video_id}.txt"
		with open(file_path, "w", encoding="utf-8") as f:
			f.write(transcript)

		# ✅ Step 7: Send result
		embed = self.build_embed(
			title="✅ Transcription Complete",
			color=discord.Color.green(),
			builder_fn=lambda e: [
				e.add_field(name="Source", value=f"[Open video]({job.clean_url})", inline=False),
				e.add_field(name="Time Taken", value=elapsed_str, inline=True),
				e.set_thumbnail(url=f"https://img.youtube.com/vi/{job.video_id}/hqdefault.jpg")
			]
		)

		if len(transcript) <= 1000:
			embed.add_field(name="Transcript", value=transcript, inline=False)
			await job.interaction.channel.send(
				content=f"{job.interaction.user.mention} The transcript is ready.",
				embed=embed
			)
		else:
			file = discord.File(file_path, filename=f"{job.video_id}.txt")
			await job.interaction.channel.send(
				content=f"{job.interaction.user.mention} Here's the transcript file.",
				embed=embed,
				file=file
			)

		self.active_jobs.remove(job)
		print(f"Finished transcription for {job.clean_url} in {elapsed_str}")

	def cog_unload(self):
		self.worker.cancel()

async def setup(bot: commands.Bot):
	await bot.add_cog(WinstonCog(bot))