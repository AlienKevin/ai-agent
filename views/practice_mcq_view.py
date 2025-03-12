import discord
import asyncio
from views.mcq_view import MCQView
from views.initial_view import InitialView

class PracticeMCQView(MCQView):
    def __init__(self, agent, question, correct_answers):
        super().__init__(agent, question, correct_answers)
        
    async def _handle_answer(self, interaction: discord.Interaction, answer):
        # Acknowledge the interaction immediately
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Start a background task for the API call
        asyncio.create_task(self._process_answer_in_background(interaction, answer, state))

    async def _process_answer_in_background(self, interaction, answer, state):
        try:
            # Get feedback for the answer (this can take time)
            response = await self.agent._handle_question_answer(answer, state)
            
            # Create feedback view with options
            from views.feedback_view import FeedbackView
            view = FeedbackView(self.agent, state["question"], state["correct_answers"])
            
            # Send feedback with options
            await interaction.followup.send(response, view=view)
        except Exception as e:
            print(f"Error processing answer: {e}")
            await interaction.followup.send(
                "I encountered an error processing your answer. Please try again.",
                view=InitialView(self.agent)
            )