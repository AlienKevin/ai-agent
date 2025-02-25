import os
import discord
import logging

from discord.ext import commands
from discord import ButtonStyle, Interaction
from discord.ui import Button, View
from dotenv import load_dotenv
from agent import MistralAgent

PREFIX = "!"

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord")

# Load the environment variables
load_dotenv()

# Create the bot with all intents
# The message content and members intent must be enabled in the Discord Developer Portal for the bot to work.
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Import the Mistral agent from the agent.py file
agent = MistralAgent()

# Get the token from the environment variables
token = os.getenv("DISCORD_TOKEN")


class MCQView(View):
    def __init__(self, channel_id):
        super().__init__(timeout=300)  # 5 minute timeout
        self.channel_id = channel_id
        
        # Add buttons for A, B, C, D options
        for option in ["A", "B", "C", "D"]:
            self.add_item(MCQButton(option))


class MCQButton(Button):
    def __init__(self, option):
        super().__init__(
            style=ButtonStyle.primary,
            label=option,
            custom_id=f"mcq_{option}"
        )
        
    async def callback(self, interaction: Interaction):
        # Process the answer
        result = agent.record_answer(
            interaction.channel_id,
            interaction.user.id,
            self.label
        )
        
        if not result:
            await interaction.response.send_message(
                "There's no active quiz or something went wrong.", 
                ephemeral=True
            )
            return
        
        # Give feedback on their answer
        if result["is_correct"]:
            feedback = f"✅ Correct! Your score: {result['score']}"
        else:
            feedback = f"❌ Incorrect. The correct answer was {result['correct_answer']}. Your score: {result['score']}"
            
        await interaction.response.send_message(feedback, ephemeral=True)
        
        # Check if we need to send the next question (only one user needs to trigger this)
        # We'll have the bot handle this in the on_interaction event


@bot.event
async def on_ready():
    """
    Called when the client is done preparing the data received from Discord.
    Prints message on terminal when bot successfully connects to discord.
    """
    logger.info(f"{bot.user} has connected to Discord!")


@bot.event
async def on_message(message: discord.Message):
    """
    Called when a message is sent in any channel the bot can see.
    """
    # Don't delete this line! It's necessary for the bot to process commands.
    await bot.process_commands(message)

    # Ignore messages from self or other bots to prevent infinite loops.
    if message.author.bot or message.content.startswith("!"):
        return

    # Process regular messages with the agent
    logger.info(f"Processing message from {message.author}: {message.content}")
    response = await agent.run(message)
    await message.reply(response)


@bot.event
async def on_interaction(interaction: Interaction):
    """Process button interactions for MCQs."""
    if not interaction.data or not interaction.data.get('custom_id', '').startswith('mcq_'):
        return

    # This will be called after the button callback has already recorded the answer
    # Now we can check if we need to send the next question
    quiz = agent.get_active_quiz(interaction.channel_id)
    
    if not quiz:
        return
        
    # Check if all users have answered this question
    # For simplicity, we'll just continue to the next question whenever any user answers
    next_question = agent.next_question(interaction.channel_id)
    
    if next_question == "end":
        # Quiz is over
        results = agent.end_quiz(interaction.channel_id)
        
        # Format scores
        scores_text = "\n".join([
            f"<@{user_id}>: {score}/{results['total_questions']}" 
            for user_id, score in sorted(results['scores'].items(), key=lambda x: x[1], reverse=True)
        ])
        
        final_message = f"📊 **Quiz Results**\n\n{scores_text}\n\nThank you for participating!"
        await interaction.channel.send(final_message)
    else:
        # Send the next question
        await send_mcq_question(interaction.channel, next_question)


async def send_mcq_question(channel, question_data):
    """Send an MCQ question to the channel with buttons for options."""
    question_num = agent.get_active_quiz(channel.id)["current_question"]
    total_questions = agent.get_active_quiz(channel.id)["total_questions"]
    
    # Format the question and options
    options_text = "\n".join([f"{key}: {value}" for key, value in question_data["options"].items()])
    question_text = f"**Question {question_num}/{total_questions}**\n\n{question_data['question']}\n\n{options_text}"
    
    # Create a view with the buttons
    view = MCQView(channel.id)
    
    # Send the question
    await channel.send(question_text, view=view)


# Commands
@bot.command(name="quiz", help="Starts a quiz on a specified topic.")
async def start_quiz(ctx, *, topic=None):
    if not topic:
        await ctx.send("Please provide a topic to generate questions on. For example: `!quiz Solar System`")
        return
        
    processing_msg = await ctx.send(f"📝 Generating MCQ questions about: **{topic}**...")
    
    try:
        # Generate MCQs from topic
        questions = await agent.generate_mcqs_from_topic(topic)
        
        # Start the quiz
        agent.start_quiz(ctx.channel.id, questions)
        
        # Update processing message
        await processing_msg.edit(content=f"✅ Generated MCQ questions about **{topic}**! Starting the quiz now...")
        
        # Send the first question
        first_question = agent.next_question(ctx.channel.id)
        await send_mcq_question(ctx.channel, first_question)
        
    except Exception as e:
        logger.error(f"Error generating quiz: {e}")
        await processing_msg.edit(content=f"⚠️ Error generating quiz: {str(e)}")


@bot.command(name="ping", help="Pings the bot.")
async def ping(ctx, *, arg=None):
    if arg is None:
        await ctx.send("Pong!")
    else:
        await ctx.send(f"Pong! Your argument was {arg}")


# Start the bot, connecting it to the gateway
bot.run(token)