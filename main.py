import os
from utils.DiscordBot import DiscordBot

from dotenv import load_dotenv
load_dotenv()

def main():
	bot = DiscordBot()
	bot.run(token = os.getenv("SECRET_TOKEN"))

if __name__ == "__main__":
	main()