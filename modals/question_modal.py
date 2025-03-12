import discord

class QuestionModal(discord.ui.Modal):
    def __init__(self, agent):
        super().__init__(title="Ask a Question")
        self.agent = agent
        self.question = discord.ui.TextInput(
            label="What's your question?",
            placeholder="Enter your question...",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        response = await self.agent._handle_question(
            self.question.value,
            state["goal"],
            state["pdf_files"]
        )
        await interaction.response.send_message(response)
