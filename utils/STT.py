import requests, os
from utils import utils

class STTClient:
	def __init__(self, host: str, port: int, endpoint: str, config, log_dir: str, debug = False):
		self.endpoint = f"http://{host}:{port}{endpoint}"
		self.session = requests.Session()

		self.beam_size = config["hyperparameters"]["beam_size"]
		self.prompt = config["prompt"]

		cmd = [
			f"{os.getenv('WHISPER_BACKEND')}\\whisper-server", "-m", config["model"],
			"-vm", config["vad"], "-fa", "--port", str(port)
		]
		# TODO does python have destructors?
		self.process = utils.start_subprocess(cmd, int(debug), log_dir)
		print(f"STT server running at: {self.endpoint}")

	def close(self):
		self.process.terminate()
		self.process.wait()
		print("STT server terminated")

	def transcribe(self, audio_b64: str) -> str:
		# Build payload for Whisper server
		payload = {
			"audio": audio_b64,
			"prompt": self.prompt,
			"suppress_non_speech": False,
			"temperature": 0.0,
			"beam_size": self.beam_size,
			"vad": True
		}
		response = self.session.post(self.endpoint, json=payload)
		response.raise_for_status()

		# Extract and return the transcript
		return response.json().get("text", "").strip()