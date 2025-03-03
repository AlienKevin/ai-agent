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


@bot.event
async def on_member_join(member):
    """
    Called when a member joins a guild (server).
    Initializes the state for the new member and sends them a welcome message.
    """
    # Don't initialize for bots
    if member.bot:
        return
        
    # Initialize state for the new member
    agent._initialize_state(str(member.id))
    
    # Check if the user has interacted with the bot before in any mutual guilds
    has_history = False
    for guild in bot.guilds:
        if member in guild.members:
            # Check channels in this guild for message history with this user
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).read_message_history:
                    try:
                        async for message in channel.history(limit=200):
                            # Check if this message is from the user and was a reply to the bot
                            # or if the bot replied to this user
                            if (message.author.id == member.id and message.reference and 
                                message.reference.resolved and message.reference.resolved.author == bot.user):
                                has_history = True
                                break
                            if (message.author == bot.user and message.reference and 
                                message.reference.resolved and message.reference.resolved.author.id == member.id):
                                has_history = True
                                break
                    except discord.errors.Forbidden:
                        continue
                
                if has_history:
                    break
        
        if has_history:
            break
    
    try:
        # Try to send a direct message to the user
        if has_history:
            # User has chatted with the bot before
            welcome_message = (
                f"👋 Welcome back to {member.guild.name}, {member.name}! I'm here to help you continue learning.\n\n"
                f"You can use the following commands:\n"
                f"- `!topic [subject]` - Start learning about a specific topic\n"
                f"- `!answer [A/B/C/D/E]` - Answer the current question\n"
                f"- `!question [question]` - Ask any question about the topic\n"
                f"- `!upload` - Upload PDF documents to study from (attach files with this command)"
            )
        else:
            # New user who hasn't chatted with the bot before
            welcome_message = (
                f"👋 Hello {member.name}! I'm your study buddy, ready to help you learn!\n\n"
                f"You can use the following commands:\n"
                f"- `!topic [subject]` - Start learning about a specific topic\n"
                f"- `!answer [A/B/C/D/E]` - Answer the current question\n"
                f"- `!question [question]` - Ask any question about the topic\n"
                f"- `!upload` - Upload PDF documents to study from (attach files with this command)\n\n"
                f"What would you like to learn about today?"
            )
        await member.send(welcome_message)
    except discord.Forbidden:
        # If we can't send a DM, try to find a channel to welcome them in
        for channel in member.guild.text_channels:
            if channel.permissions_for(member.guild.me).send_messages:
                if has_history:
                    await channel.send(f"Welcome back {member.mention}! I'm here to help you continue learning. Use `!topic [subject]` to start!")
                else:
                    await channel.send(f"Welcome {member.mention}! I'm your study buddy. Use `!topic [subject]` to start learning!")
                break


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


@bot.command(name="answer", help="Answer the current question (A, B, C, D, or E) or 'not sure'")
async def answer(ctx, *, option):
    # Modify the message content to use the !answer command format
    ctx.message.content = f"!answer {option}"
    # Process with the agent
    response = await agent.run(ctx.message)
    await ctx.send(response)


@bot.command(name="question", help="Ask any question about the topic")
async def question(ctx, *, query):
    # Modify the message content to use the !question command format
    ctx.message.content = f"!question {query}"
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
