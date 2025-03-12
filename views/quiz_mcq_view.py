import discord
import asyncio
import time
from discord.ui import Button
from views.mcq_view import MCQView
from views.initial_view import InitialView
from model import QuizState, UserState

class QuizMCQView(MCQView):
    def __init__(self, agent, question_text: str, correct_answers: list, quiz_state: QuizState):
        super().__init__(agent, question_text, correct_answers)
        self.quiz_state = quiz_state
        
        # Start the timer update task using the asyncio event loop
        self.timer_task = asyncio.get_event_loop().create_task(self.update_timer())
    
    async def update_timer(self):
        """Update the timer display every second"""
        try:
            while not self.quiz_state.is_finished() and not self.is_finished():
                # Wait for 1 second
                await asyncio.sleep(1)
                
                # Try to update the message with the new view
                # This might fail if the view is no longer being displayed
                try:
                    if hasattr(self, 'message') and self.message:
                        await self.message.edit(view=self)
                except Exception as e:
                    print(f"Error updating timer: {e}")
                    break
                    
            # If we exited because the quiz finished, handle it
            if self.quiz_state.is_finished() and hasattr(self, 'message') and self.message:
                try:
                    # Get the user ID from the message
                    if hasattr(self.message, 'interaction') and self.message.interaction:
                        user_id = str(self.message.interaction.user.id)
                        state = self.agent.conversation_state.get(user_id)
                        
                        if state and state.get("quiz_state") == self.quiz_state:
                            # Grade the quiz
                            response, view = await self.agent._grade_quiz(state)
                            
                            # Reset state but keep PDF files
                            pdf_files = state.get("pdf_files", [])
                            self.agent.conversation_state[user_id] = {
                                "state": UserState.INITIAL,
                                "goal": None,
                                "question": None,
                                "correct_answers": [],
                                "question_history": [],
                                "pdf_files": pdf_files,
                                "quiz_state": None
                            }
                            
                            # Show a new message with the results
                            await self.message.channel.send(
                                f"{response}\n\nTime's up! Quiz has ended. What would you like to do next?",
                                view=InitialView(self.agent)
                            )
                except Exception as e:
                    print(f"Error handling quiz end: {e}")
        except asyncio.CancelledError:
            # Task was cancelled, clean up
            pass
        except Exception as e:
            print(f"Error in timer task: {e}")
    
    @discord.ui.button(label="End Quiz", style=discord.ButtonStyle.danger, custom_id="end_quiz", row=4)
    async def end_quiz_button(self, interaction: discord.Interaction, button: Button):
        # Cancel the timer task
        if hasattr(self, 'timer_task') and not self.timer_task.done():
            self.timer_task.cancel()
            
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Grade the quiz and get the first question feedback
        response, view = await self.agent._grade_quiz(state)
        
        # Show quiz results with the first question feedback
        await interaction.response.send_message(response, view=view)

    async def _handle_answer(self, interaction: discord.Interaction, answer):
        # Cancel the timer task for this view since we're moving to a new question
        if hasattr(self, 'timer_task') and not self.timer_task.done():
            self.timer_task.cancel()
        
        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        quiz_state = state["quiz_state"]
        
        # Check if quiz is finished
        if quiz_state.is_finished():
            # Quiz is over, grade it
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            response, view = await self.agent._grade_quiz(state)
            await interaction.followup.send(response, view=view)
            return
        
        # Generate next question
        try:
            # Get feedback for the answer (this can take time)
            response = await self.agent._handle_question_answer(answer, state)

            quiz_state.total_questions += 1
            # Create view for next question with updated timer
            next_view = QuizMCQView(self.agent, state["question"], state["correct_answers"], quiz_state)
            
            # Show time remaining with each question
            message = (
                f"Answer recorded. Times up <t:{int(time.time()//1 + quiz_state.time_remaining())}:R>\n\n"
                f"Next question:\n{state['question']}"
            )
            
            await interaction.followup.send(message, view=next_view)
            
        except Exception as e:
            print(f"Error generating next question: {e}")
            # End the quiz early if there's an error
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            await interaction.followup.send(
                "I encountered an error generating the next question. The quiz has been ended.",
                view=InitialView(self.agent)
            )
