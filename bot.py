import os
import discord
import logging

from discord.ext import commands
from dotenv import load_dotenv
from agent import StudyAgent, Command

PREFIX = "!"

# Setup logging
logger = logging.getLogger("discord")

# Load the environment variables
load_dotenv()

# Create the bot with all intents
# The message content and members intent must be enabled in the Discord Developer Portal for the bot to work.
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Import the Mistral agent from the agent.py file
agent = StudyAgent()


# Get the token from the environment variables
token = os.getenv("DISCORD_TOKEN")


@bot.event
async def on_ready():
    """
    Called when the client is done preparing the data received from Discord.
    Prints message on terminal when bot successfully connects to discord.

    https://discordpy.readthedocs.io/en/latest/api.html#discord.on_ready
    """
    logger.info(f"{bot.user} has connected to Discord!")


@bot.event
async def on_message(message: discord.Message):
    """
    Called when a message is sent in any channel the bot can see.

    https://discordpy.readthedocs.io/en/latest/api.html#discord.on_message
    """
    # Don't delete this line! It's necessary for the bot to process commands.
    # This will handle all commands that start with the PREFIX (!)
    await bot.process_commands(message)

    # Ignore messages from self or other bots to prevent infinite loops.
    if message.author.bot:
        return
        
    # Ignore messages that start with the command prefix
    # Commands are handled by the command system above
    if message.content.startswith(PREFIX):
        return

    # Process regular messages with the agent
    logger.info(f"Processing message from {message.author}: {message.content}")
    response = await agent.run(message)

    # Send the response back to the channel
    await message.reply(response)


# Commands

# This example command is here to show you how to add commands to the bot.
@bot.command(name="ping", help="Pings the bot.")
async def ping(ctx, *, arg=None):
    if arg is None:
        await ctx.send("Pong!")
    else:
        await ctx.send(f"Pong! Your argument was {arg}")


# Register agent commands as bot commands
@bot.command(name="topic", help="Start learning about a specific topic")
async def topic(ctx, *, subject):
    # Modify the message content to use the !topic command format
    ctx.message.content = f"!topic {subject}"
    # Process with the agent
    response = await agent.run(ctx.message)
    await ctx.send(response)


@bot.command(name="answer", help="Answer the current question (A, B, C, D, or E)")
async def answer(ctx, option):
    # Modify the message content to use the !answer command format
    ctx.message.content = f"!answer {option}"
    # Process with the agent
    response = await agent.run(ctx.message)
    await ctx.send(response)


@bot.command(name="followup", help="Ask a followup question about the topic")
async def followup(ctx, *, question):
    # Modify the message content to use the !followup command format
    ctx.message.content = f"!followup {question}"
    # Process with the agent
    response = await agent.run(ctx.message)
    await ctx.send(response)


@bot.command(name="upload", help="Upload PDF documents to study from")
async def upload(ctx):
    # Modify the message content to use the !upload command format
    ctx.message.content = "!upload"
    # Process with the agent
    response = await agent.run(ctx.message)
    await ctx.send(response)


# Start the bot, connecting it to the gateway
bot.run(token)
