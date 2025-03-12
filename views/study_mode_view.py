import discord
from discord.ui import View, Button
from modals.quiz_duration_modal import QuizDurationModal
from views.practice_mcq_view import PracticeMCQView
from views.initial_view import InitialView
from model import UserState

class StudyModeView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Start Quiz", style=discord.ButtonStyle.success)
    async def quiz_button(self, interaction: discord.Interaction, button: Button):
        modal = QuizDurationModal(self.agent)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="General Practice", style=discord.ButtonStyle.primary)
    async def practice_button(self, interaction: discord.Interaction, button: Button):
        try:
            user_id = str(interaction.user.id)
            state = self.agent.conversation_state[user_id]
            
            # Acknowledge the interaction immediately to prevent timeout
            await interaction.response.defer(thinking=True)
            
            # Generate first question
            question, correct_answers = await self.agent._generate_question(
                state["goal"],
                state["question_history"],
                state["pdf_files"]
            )
            
            state["question"] = question
            state["correct_answers"] = correct_answers
            state["state"] = UserState.ASKING_QUESTION
            
            # Create MCQ view with end session button
            view = PracticeMCQView(self.agent, question, correct_answers)
            
            # Follow up with the actual message after the question is generated
            await interaction.followup.send(question, view=view)
        except Exception as e:
            print(f"Error in practice_button: {e}")
            await interaction.followup.send(
                "I encountered an error processing your request. Please try again.",
                ephemeral=True,
                view=InitialView(self.agent),
            )

