import discord
from model import QuizState, UserState
from views.quiz_mcq_view import QuizMCQView
import time

class QuizDurationModal(discord.ui.Modal):
    def __init__(self, agent):
        super().__init__(title="Quiz Duration")
        self.agent = agent
        self.duration = discord.ui.TextInput(
            label="Quiz Duration (minutes)",
            placeholder="Enter a number between 1-60",
            required=True,
            max_length=2
        )
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Acknowledge the interaction immediately to prevent timeout
            await interaction.response.defer(thinking=True)
            
            duration = int(self.duration.value)
            if duration < 1 or duration > 60:
                await interaction.followup.send(
                    "Quiz duration must be between 1 and 60 minutes.",
                    ephemeral=True
                )
                return
                
            user_id = str(interaction.user.id)
            state = self.agent.conversation_state[user_id]
            
            # Initialize quiz state
            quiz_state = QuizState(duration, state["goal"])
            state["quiz_state"] = quiz_state
            state["state"] = UserState.IN_QUIZ
            
            # Generate first question
            try:
                question, correct_answers = await self.agent._generate_question(
                    state["goal"],
                    state["question_history"],
                    state["pdf_files"]
                )

                state["question"] = question
                state["correct_answers"] = correct_answers
                
                # Create MCQ view for quiz
                view = QuizMCQView(self.agent, question, correct_answers, quiz_state)
                
                # Format message with timer
                message = (
                    f"Starting {duration}-minute quiz on {state['goal']}\n"
                    f"Times up <t:{int(time.time()//1 + quiz_state.time_remaining())}:R>\n\n"
                    f"{question}"
                )
                
                # Send the message using followup since we deferred
                await interaction.followup.send(message, view=view)
                
            except Exception as e:
                print(f"Error in quiz generation: {e}")
                # Reset state
                state["state"] = UserState.ASKING_QUESTION
                state["quiz_state"] = None
                
                await interaction.followup.send(
                    "I encountered an error starting your quiz. Please try again."
                )
                
        except ValueError:
            # If we haven't responded yet, use response
            try:
                await interaction.response.send_message(
                    "Please enter a valid number for the quiz duration.",
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                # If we've already responded, use followup
                await interaction.followup.send(
                    "Please enter a valid number for the quiz duration.",
                    ephemeral=True
                )