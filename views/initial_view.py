import discord
from discord.ui import View, Button
from model import UserState
from modals.goal_modal import GoalModal

class InitialView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Set Learning Goal", style=discord.ButtonStyle.primary)
    async def goal_button(self, interaction: discord.Interaction, button: Button):
        try:
            modal = GoalModal(self.agent)
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"Error in goal_button: {e}")
            await interaction.followup.send(
                "I encountered an error processing your set learning goal request.",
                ephemeral=True,
                view=InitialView(self.agent),
            )
        
    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger)
    async def end_session_button(self, interaction: discord.Interaction, button: Button):
        try:
            user_id = str(interaction.user.id)
            
            # Keep PDF files but reset everything else
            pdf_files = self.agent.conversation_state.get(user_id, {}).get("pdf_files", [])
            
            # Reset to initial state
            self.agent.conversation_state[user_id] = {
                "state": UserState.INITIAL,
                "goal": None,
                "question": None,
                "correct_answers": [],
                "question_history": [],
                "pdf_files": pdf_files,
                "quiz_state": None
            }
            
            await interaction.response.send_message(
                "Session ended. All progress has been reset.",
                view=InitialView(self.agent)
            )
        except Exception as e:
            print(f"Error in end_session_button: {e}")
            await interaction.followup.send(
                "I encountered an error processing your end session request.",
                ephemeral=True,
                view=InitialView(self.agent),
            )
