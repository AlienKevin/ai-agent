import discord
from model import MODEL

class FollowUpQuestionModal(discord.ui.Modal):
    def __init__(self, agent, next_question, next_correct_answers):
        super().__init__(title="Ask a Follow-up Question")
        self.agent = agent
        self.next_question = next_question
        self.next_correct_answers = next_correct_answers
        
        self.question = discord.ui.TextInput(
            label="What's your follow-up question?",
            placeholder="Enter your question...",
            required=True,
            max_length=1000,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Acknowledge the interaction immediately to prevent timeout
            await interaction.response.defer(ephemeral=False)
            
            user_id = str(interaction.user.id)
            state = self.agent.conversation_state[user_id]
            
            # Get the previous question and correct answers for context
            previous_question = state.get("question", "")
            previous_correct_answers = state.get("correct_answers", [])
            
            # Format the correct answers for display
            if isinstance(previous_correct_answers, list):
                if len(previous_correct_answers) == 1:
                    correct_answer_display = previous_correct_answers[0]
                else:
                    correct_answer_display = ", ".join(sorted(previous_correct_answers))
            else:
                correct_answer_display = str(previous_correct_answers)
            
            # Create a detailed prompt with context
            prompt = f"""The student is learning about {state.get('goal', 'the subject')} and has asked a follow-up question.

Previous question: {previous_question}
Correct answer: {correct_answer_display}

Follow-up question: {self.question.value}

Please provide a clear, helpful answer to this follow-up question, specifically addressing the student's question in the context of the previous question and answer. Focus on explaining the concept in a way that's easy to understand.

IMPORTANT: Keep your response under 1500 characters to fit within Discord's message limits."""
            
            # Generate the response
            response = self.agent.client.models.generate_content(
                model=MODEL,
                contents=[prompt],
                config={
                    "max_output_tokens": 400,  # Reduced to ensure we stay under Discord's limit
                    "temperature": 0.7
                }
            )
            
            # Truncate response if it's still too long
            response_text = response.text
            if len(response_text) > 1900:  # Leave some room for formatting
                response_text = response_text[:1900] + "... (truncated)"
            
            # Create feedback view for next steps
            from views.feedback_view import FeedbackView
            view = FeedbackView(self.agent, self.next_question, self.next_correct_answers)
            
            # Send the response
            await interaction.followup.send(response_text, view=view)
            
        except Exception as e:
            print(f"Error in follow-up question: {e}")
            await interaction.followup.send(
                f"I'm sorry, I encountered an error processing your follow-up question: {str(e)}. Please try again with a different question.",
                ephemeral=True
            )
