import discord
from discord.ui import View, Button
from views.initial_view import InitialView
from model import MODEL, UserState

class FeedbackView(View):
    def __init__(self, agent, next_question, next_correct_answers):
        super().__init__(timeout=None)
        self.agent = agent
        self.next_question = next_question
        self.next_correct_answers = next_correct_answers

    @discord.ui.button(label="Next Question", style=discord.ButtonStyle.primary)
    async def next_question_button(self, interaction: discord.Interaction, button: Button):
        # Import here to avoid circular import
        from views.practice_mcq_view import PracticeMCQView
        
        # Create view for next question
        view = PracticeMCQView(self.agent, self.next_question, self.next_correct_answers)
        
        # Update the user's state with the new question
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        state["question"] = self.next_question
        state["correct_answers"] = self.next_correct_answers
        
        await interaction.response.send_message(self.next_question, view=view)
    
    @discord.ui.button(label="Ask Follow-up Question", style=discord.ButtonStyle.secondary)
    async def follow_up_button(self, interaction: discord.Interaction, button: Button):
        from modals.followup_question_modal import FollowUpQuestionModal
        modal = FollowUpQuestionModal(self.agent, self.next_question, self.next_correct_answers)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger)
    async def end_session_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Generate learning summary before resetting state
        summary = await self._generate_learning_summary(state)
        
        # Reset state but keep PDF files
        pdf_files = state.get("pdf_files", [])
        self.agent.conversation_state[user_id] = {
            "state": UserState.INITIAL,
            "goal": None,
            "question": None,
            "correct_answers": [],
            "question_history": [],
            "pdf_files": pdf_files
        }
        
        # Show summary and initial view
        await interaction.response.send_message(
            f"**Learning Session Summary**\n\n{summary}\n\nSession ended. What would you like to do next?",
            view=InitialView(self.agent)
        )
    
    async def _generate_learning_summary(self, state):
        """Generate a summary of the learning session"""
        question_history = state.get("question_history", [])
        goal = state.get("goal", "Unknown topic")
        
        if not question_history:
            return "No questions were answered in this session."
        
        # Calculate statistics
        total_questions = len(question_history)
        correct_answers = sum(1 for q in question_history if q.get("is_correct", False))
        incorrect_answers = total_questions - correct_answers
        accuracy = (correct_answers / total_questions) * 100 if total_questions > 0 else 0
        
        # Collect concepts that were answered incorrectly
        incorrect_concepts = {}
        for q in question_history:
            if not q.get("is_correct", False):
                concept = q.get("concept", "Unknown concept")
                incorrect_concepts[concept] = incorrect_concepts.get(concept, 0) + 1
        
        # Sort concepts by frequency
        review_concepts = sorted(incorrect_concepts.items(), key=lambda x: x[1], reverse=True)
        
        # Build summary text
        summary = [
            f"**Topic:** {goal}",
            f"**Questions Answered:** {total_questions}",
            f"**Correct Answers:** {correct_answers}",
            f"**Incorrect Answers:** {incorrect_answers}",
            f"**Accuracy:** {accuracy:.1f}%",
        ]
        
        # Add review recommendations if there were incorrect answers
        if incorrect_answers > 0:
            summary.append("\n**Concepts to Review:**")
            for concept, count in review_concepts:
                summary.append(f"• {concept} ({count} incorrect)")
        
        # Generate personalized feedback using Gemini
        if total_questions >= 3:  # Only generate AI feedback if enough questions were answered
            try:
                # Create a prompt for Gemini
                concepts_tested = [q.get("concept", "Unknown") for q in question_history]
                correct_concepts = [q.get("concept", "Unknown") for q in question_history if q.get("is_correct", False)]
                incorrect_concepts = [q.get("concept", "Unknown") for q in question_history if not q.get("is_correct", False)]
                
                prompt = (
                    f"The student has completed a learning session on '{goal}'.\n\n"
                    f"They answered {total_questions} questions with {correct_answers} correct and {incorrect_answers} incorrect.\n\n"
                    f"Concepts tested: {', '.join(set(concepts_tested))}\n"
                    f"Concepts they understood well: {', '.join(set(correct_concepts))}\n"
                    f"Concepts they struggled with: {', '.join(set(incorrect_concepts))}\n\n"
                    f"Please provide a brief, encouraging summary (2-3 sentences) of their performance and 1-2 specific suggestions for what to focus on next."
                )
                
                response = self.agent.client.models.generate_content(
                    model=MODEL,
                    contents=[prompt],
                    config={
                        "max_output_tokens": 200,
                        "temperature": 0.7
                    }
                )
                
                summary.append(f"\n**AI Feedback:**\n{response.text}")
            except Exception as e:
                print(f"Error generating AI feedback: {e}")
        
        return "\n".join(summary)

