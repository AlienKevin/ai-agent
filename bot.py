import os
import discord
import logging
import asyncio

from discord.ext import commands
from dotenv import load_dotenv
from agent import StudyAgent

PREFIX = "!"

# Setup logging
logger = logging.getLogger("discord")

# Load the environment variables
load_dotenv()

class StudyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix=PREFIX, intents=intents)
        self.agent = StudyAgent()

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_message(self, message):
        if message.author.bot:
            return

        if message.content.startswith("!clear"):
            channel = message.channel
            await message.delete()  # Delete the command message

            deleted = await channel.purge(limit=100)  # Delete last 100 messages
            return

        # Process with agent
        response = await self.agent.run(message)
        if response:  # Only send if there's a response
            await message.channel.send(response)

# Update main section
def main():
    load_dotenv()
    bot = StudyBot()
    bot.run(os.getenv('DISCORD_TOKEN'))

if __name__ == "__main__":
    main()
