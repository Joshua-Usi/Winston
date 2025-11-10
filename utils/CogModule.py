import discord
from discord.ext import commands
import os
import random

class CogModule(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot
		raw = os.getenv("OWNER_IDS")
		self.OWNER_IDS = list(map(int, raw.split(",")))

	def is_owner(self, interaction: discord.Interaction) -> bool:
		"""Check if the user is the management list."""
		return interaction.user.id in self.OWNER_IDS