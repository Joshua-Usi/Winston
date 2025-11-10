import os
import importlib
import discord
from discord.ext import commands
import logging

class DiscordBot(commands.Bot):
	def __init__(self, cogs_folder: str = "cogs", sync_mode: bool = False):
		intents = discord.Intents.default()
		intents.messages = True
		intents.message_content = True
		intents.guilds = True
		intents.voice_states = True

		super().__init__(command_prefix="!", intents=intents)
		self._cogs_folder = cogs_folder
		self._sync_mode = sync_mode

	async def setup_hook(self):
		"""Hook for asynchronous setup during bot initialization."""
		await self._load_cogs()

		if self._sync_mode:
			await self.manual_sync_commands()
			await self.close()

	async def _load_cogs(self):
		"""Automatically load all Cogs from Python files in the specified directory."""
		for file_name in os.listdir(self._cogs_folder):
			if not file_name.endswith(".py") or file_name.startswith("_"):
				continue

			cog_name = file_name[:-3]  # Strip '.py'
			try:
				module = importlib.import_module(f"{self._cogs_folder}.{cog_name}")
				if hasattr(module, "setup"):
					await module.setup(self)
					print(f"Loaded Cog: {cog_name}")
				else:
					raise RuntimeError(f"No setup function found in {cog_name}")
			except Exception as e:
				print(f"Failed to load Cog {cog_name}: {e}")
				raise  # Re-raise the exception to terminate the bot


	async def on_ready(self):
		print(f"{self.user} has connected to Discord!")

	async def manual_sync_commands(self):
		"""Globally synchronise commands with discord"""
		synced_commands = await self.tree.sync()

		print(f"Synced {len(synced_commands)} commands:")
		for command in synced_commands:
			print(f"- {command.name}")

	async def close(self):
		"""Override so cog's call cog.unload()"""
		for cog_name in list(self.cogs.keys()):
			await self.remove_cog(cog_name)
		await super().close() 