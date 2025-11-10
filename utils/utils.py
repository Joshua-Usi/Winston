import os, subprocess, threading, socket
from datetime import datetime
from pathlib import Path

def _find_next_free_port():
	s = socket.socket()
	s.bind(('', 0))
	port = s.getsockname()[1]
	s.close()
	return port

def get_free_port():
	# Try 6 times, it can be racy that's why. Not a gaurantee but it's a 1 in a billion ish chance
	for _ in range(6):
		p = _find_next_free_port()
		if p:
			return p
	raise RuntimeError("Unable to allocate port")

IGNORED_CODES = {0, -15, 1, 3221225786}

def _timestamp() -> str:
	return datetime.now().strftime("%Y-%m-%d-%H-%M")

def _basename(prog: str) -> str:
	return os.path.splitext(os.path.basename(prog))[0] or "subprocess"

def _ensure_dir(path: str):
	Path(path).mkdir(parents=True, exist_ok=True)

def start_subprocess(cmd, debug_mode: bool = False, log_dir: str = "."):
	_program = _basename(cmd[0] if isinstance(cmd, (list, tuple)) and cmd else str(cmd))
	_ensure_dir(log_dir)

	try:
		if debug_mode:
			creation = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
			p = subprocess.Popen(cmd, creationflags=creation if creation else 0)
			def _wait_and_report():
				code = p.wait()
				if code != 0:
					print(f"[subprocess] subprocess by the name {_program} crashed with code {code}")
			threading.Thread(target=_wait_and_report, daemon=True).start()
			return p
		else:
			p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")

			def _drain_and_maybe_log():
				out, err = p.communicate()
				code = p.returncode
				if code not in IGNORED_CODES:
					log_path = os.path.join(log_dir, f"{_timestamp()}-{_program}.log")
					with open(log_path, "w", encoding="utf-8") as f:
						f.write(f"# Command\n{cmd}\n\n")
						f.write(f"# Exit code\n{code}\n\n")
						f.write("# STDOUT\n")
						f.write(out or "")
						f.write("\n\n# STDERR\n")
						f.write(err or "")
						print(f"[subprocess] subprocess by the name {_program} crashed with code {code}")

			threading.Thread(target=_drain_and_maybe_log, daemon=True).start()
			return p

	except Exception as e:
		log_path = os.path.join(log_dir, f"{_timestamp()}-{_program}.log")
		with open(log_path, "w", encoding="utf-8") as f:
			f.write(f"# Command\n{cmd}\n\n")
			f.write("# Startup failure\n")
			f.write(repr(e))
		print(f"[subprocess] subprocess by the name {_program} crashed with code (failed to start)")
		raise
