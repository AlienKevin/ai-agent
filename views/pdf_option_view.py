import discord
from discord.ui import View, Button
from model import UserState
from views.initial_view import InitialView

class PDFOptionView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Upload PDF", style=discord.ButtonStyle.secondary)
    async def upload_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "Please upload your PDF files by dragging them here or clicking the upload button.",
            ephemeral=True
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        # Move to study mode selection
        from views.study_mode_view import StudyModeView
        await interaction.response.send_message(
            "Please select your study mode:",
            view=StudyModeView(self.agent)
        )
        
    @discord.ui.button(label="Change Learning Goal", style=discord.ButtonStyle.primary)
    async def change_goal_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        
        # Reset state but keep PDF files
        pdf_files = self.agent.conversation_state[user_id].get("pdf_files", [])
        self.agent.conversation_state[user_id] = {
            "state": UserState.INITIAL,
            "goal": None,
            "question": None,
            "correct_answers": [],
            "question_history": [],
            "pdf_files": pdf_files
        }
        
        # Go back to initial view
        await interaction.response.send_message(
            "Let's set a new learning goal.",
            view=InitialView(self.agent)
        )
