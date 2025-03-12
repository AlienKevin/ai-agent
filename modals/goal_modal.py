import discord

class GoalModal(discord.ui.Modal):
    def __init__(self, agent):
        super().__init__(title="Set Learning Goal")
        self.agent = agent
        self.goal = discord.ui.TextInput(
            label="What would you like to learn about?",
            placeholder="Enter your learning goal...",
            required=True,
            max_length=200
        )
        self.add_item(self.goal)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Update the state with the new goal
        state["goal"] = self.goal.value
        
        # Show PDF upload option
        from views.pdf_option_view import PDFOptionView
        await interaction.response.send_message(
            f"Learning goal set: {self.goal.value}\n\nWould you like to upload a PDF to study from?",
            view=PDFOptionView(self.agent)
        )