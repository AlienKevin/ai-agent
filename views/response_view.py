import discord
from discord.ui import View, Button
from modals.goal_modal import GoalModal
from modals.quiz_duration_modal import QuizDurationModal

class ResponseView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Set New Goal", style=discord.ButtonStyle.primary)
    async def goal_button(self, interaction: discord.Interaction, button: Button):
        modal = GoalModal(self.agent)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Start Quiz", style=discord.ButtonStyle.success)
    async def quiz_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        if not state.get("goal"):
            await interaction.response.send_message(
                "Please set a learning goal first!",
                ephemeral=True
            )
            return
            
        modal = QuizDurationModal(self.agent)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Upload PDFs", style=discord.ButtonStyle.secondary)
    async def upload_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "Please upload your PDF files by dragging them here or clicking the upload button.",
            ephemeral=True
        )
