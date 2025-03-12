import discord
from discord.ui import View, Button
from modals.goal_modal import GoalModal

class QuizResultsView(View):
    def __init__(self, agent, results, current_index=0):
        super().__init__(timeout=None)
        self.agent = agent
        self.results = results
        self.current_index = current_index
        self.total_questions = len(results)
        
        # Update button states based on current index
        self.update_button_states()
        
    def update_button_states(self):
        # Disable prev button if at first question
        self.prev_button.disabled = (self.current_index == 0)
        
        # Disable next button if at last question
        self.next_button.disabled = (self.current_index == self.total_questions - 1)
    
    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: Button):
        self.current_index = max(0, self.current_index - 1)
        self.update_button_states()
        
        # Get the feedback for the current question
        feedback = self._get_current_feedback()
        
        # Update the message with the new view
        await interaction.response.edit_message(content=feedback, view=self)
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, disabled=False)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_index = min(self.total_questions - 1, self.current_index + 1)
        self.update_button_states()
        
        # Get the feedback for the current question
        feedback = self._get_current_feedback()
        
        # Update the message with the new view
        await interaction.response.edit_message(content=feedback, view=self)
    
    @discord.ui.button(label="New Goal", style=discord.ButtonStyle.success)
    async def new_goal_button(self, interaction: discord.Interaction, button: Button):
        modal = GoalModal(self.agent)
        await interaction.response.send_modal(modal)
    
    def _get_current_feedback(self):
        """Get the feedback for the current question"""
        if not self.results or self.current_index >= len(self.results):
            return "No question data available."
            
        result = self.results[self.current_index]
        
        # Truncate question if it's too long
        question = result['question']
        if len(question) > 1000:  # Truncate very long questions
            question = question[:1000] + "...\n[Question truncated due to length]"
        
        feedback = (
            f"**Question {self.current_index + 1} of {self.total_questions}**\n\n"
            f"{question}\n\n"
            f"Your answer: {result['user_answer']}\n"
            f"Correct answer: {', '.join(result['correct_answers'])}\n"
            f"Result: {'✅ Correct' if result['is_correct'] else '❌ Incorrect'}\n"
            f"Concept: {result['concept']}\n"
        )
        
        return feedback