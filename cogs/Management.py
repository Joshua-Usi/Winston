from utils.CogModule import CogModule
import discord
from discord.ext import commands

class ManagementCog(CogModule):
	"""Management cog with management commands"""
	def __init__(self, bot: commands.Bot):
		super().__init__(bot)
		self.bot = bot

	@discord.app_commands.command(name="sync", description="Manually sync bot commands with Discord")
	async def sync(self, interaction: discord.Interaction):
		"""Slash command to sync bot commands."""
		if not self.is_owner(interaction):
			await self.send_unauthorised_message(interaction)
			return

		synced_commands = await self.bot.tree.sync()
		print("Commands synced!")
		await interaction.response.send_message(f"Synced {len(synced_commands)} commands:\n" + "\n".join(f"- {cmd.name}" for cmd in synced_commands), ephemeral=True)

	@discord.app_commands.command(name="shutdown", description="Gracefully shut down emma-kyu")
	async def shutdown(self, interaction: discord.Interaction):
		"""Slash command to shut down the bot."""
		if not self.is_owner(interaction):
			await self.send_unauthorised_message(interaction)
			return

		await interaction.response.send_message("Goodbye for now!")
		await self.bot.close()

async def setup(bot: commands.Bot):
	await bot.add_cog(ManagementCog(bot))