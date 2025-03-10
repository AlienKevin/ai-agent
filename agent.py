import os
#import google.generativeai as genai
#from google.generativeai import types
from google import genai
from google.genai import types
import discord
import json
import pathlib
from enum import Enum, auto
import re
import hashlib
import time
from discord.ui import Button, View
from typing import Optional, List
import asyncio

MODEL = "gemini-2.0-flash"

SYSTEM_PROMPT = """You are a StudyAgent that helps students learn. Follow these steps:
1. If the user hasn't specified a goal yet, ask them what learning goal they want to achieve
2. Generate multiple choice questions to test their understanding, with these priorities:
   a) Focus on areas they've struggled with in previous questions
   b) Ensure comprehensive coverage of all parts of the specified goal
   c) Systematically explore different aspects of the goal, even those not yet tested
3. When they answer, grade their response and provide helpful feedback
4. Continue with more questions on the same goal until they want to switch goals
5. Always ground your questions and answers in the uploaded documents (PDF files) if available - these may be past exams, lecture slides, or textbook content
6. Prioritize content from the uploaded documents when creating questions
7. Make sure to cover ALL parts of the goal mentioned in the uploaded documents

⚠️ CRITICAL REQUIREMENT: CREATE FULLY SELF-CONTAINED QUESTIONS ⚠️
- All questions MUST be completely self-contained in text form
- NEVER reference figures, images, diagrams, or visual elements that cannot be fully described in text
- If the document contains visual elements, either fully describe them in text or avoid questions that depend on them
- Users should NEVER need to look at the original documents to understand or answer questions
- NEVER say "refer to figure X" or "as shown in the diagram" or similar phrases
- If a concept relies heavily on visual elements that cannot be adequately described in text, choose a different concept to test

Keep track of their performance through question history to adapt questions to their needs. Your goal is to help them master difficult concepts while ensuring comprehensive coverage of the entire goal."""

class QuizState:
    def __init__(self, duration_minutes: int, goal: str):
        self.start_time = time.time()
        self.duration_minutes = duration_minutes
        self.end_time = self.start_time + (duration_minutes * 60)
        self.questions = []  # List of (question_text, correct_answers, concept) tuples
        self.user_answers = []  # List of user's answers
        self.goal = goal
        self.current_question_index = 0
        self.is_active = True

    def time_remaining(self) -> int:
        """Returns remaining time in seconds"""
        return max(0, int(self.end_time - time.time()))

    def is_finished(self) -> bool:
        """Check if quiz time has expired"""
        return time.time() >= self.end_time

    def format_time_remaining(self) -> str:
        """Format remaining time as MM:SS"""
        seconds = self.time_remaining()
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

class UserState(Enum):
    INITIAL = auto()
    ASKING_QUESTION = auto()
    AWAITING_ANSWER = auto()
    IN_QUIZ = auto()

class Command(Enum):
    ANSWER = "answer"
    ASK = "ask"
    GOAL = "goal"
    UPLOAD = "upload"
    QUIZ = "quiz"
    NONE = "none"

class InitialView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Set Learning Goal", style=discord.ButtonStyle.primary)
    async def goal_button(self, interaction: discord.Interaction, button: Button):
        modal = GoalModal(self.agent)
        await interaction.response.send_modal(modal)

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

class StudyModeView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Start Quiz", style=discord.ButtonStyle.success)
    async def quiz_button(self, interaction: discord.Interaction, button: Button):
        modal = QuizDurationModal(self.agent)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="General Practice", style=discord.ButtonStyle.primary)
    async def practice_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Generate first question
        question, correct_answers, _ = await self.agent._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        state["question"] = question
        state["correct_answers"] = correct_answers
        state["state"] = UserState.ASKING_QUESTION
        
        # Create MCQ view with end session button
        view = PracticeMCQView(self.agent, question, correct_answers)
        
        await interaction.response.send_message(question, view=view)

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
        await interaction.response.send_message(
            f"Learning goal set: {self.goal.value}\n\nWould you like to upload a PDF to study from?",
            view=PDFOptionView(self.agent)
        )

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

class FeedbackView(View):
    def __init__(self, agent, next_question, next_correct_answers):
        super().__init__(timeout=None)
        self.agent = agent
        self.next_question = next_question
        self.next_correct_answers = next_correct_answers

    @discord.ui.button(label="Next Question", style=discord.ButtonStyle.primary)
    async def next_question_button(self, interaction: discord.Interaction, button: Button):
        # Create view for next question
        view = PracticeMCQView(self.agent, self.next_question, self.next_correct_answers)
        
        # Update the user's state with the new question
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        state["question"] = self.next_question
        state["correct_answers"] = self.next_correct_answers
        
        await interaction.response.send_message(self.next_question, view=view)

    @discord.ui.button(label="Ask Follow-up Question", style=discord.ButtonStyle.success)
    async def followup_button(self, interaction: discord.Interaction, button: Button):
        modal = FollowUpQuestionModal(self.agent, self.next_question, self.next_correct_answers)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger)
    async def end_session_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        
        # Generate learning summary before resetting state
        summary = await self._generate_learning_summary(self.agent.conversation_state[user_id])
        
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
            await interaction.response.defer(ephemeral=True)
            
            user_id = str(interaction.user.id)
            state = self.agent.conversation_state[user_id]
            
            # Create a simpler prompt without trying to get previous feedback
            custom_prompt = (
                f"The student is learning about {state['goal']} and has asked a follow-up question about the previous question.\n\n"
                f"Previous question: {state['question']}\n\n"
                f"Follow-up question: {self.question.value}\n\n"
                f"Please answer this follow-up question clearly and concisely, addressing the specific points of confusion."
            )
            
            # Handle the follow-up question with the custom prompt
            response = self.agent.client.models.generate_content(
                model=MODEL,
                contents=[custom_prompt],
                config={
                    "max_output_tokens": 500,
                    "temperature": 0.7
                }
            )
            
            response_text = response.text
            
            # Create feedback view for next steps
            view = FeedbackView(self.agent, self.next_question, self.next_correct_answers)
            
            # Use followup.send instead of response.send_message since we deferred
            await interaction.followup.send(response_text, view=view)
            
        except Exception as e:
            # If any error occurs, send a simple error message
            print(f"Error in follow-up question: {e}")
            
            # Use followup.send since we deferred the response
            await interaction.followup.send(
                "I'm sorry, I encountered an error processing your follow-up question. Please try again with a different question.",
                ephemeral=True
            )

class MCQView(View):
    def __init__(self, agent, question_text: str, correct_answers: list):
        super().__init__(timeout=None)
        self.agent = agent
        self.question_text = question_text
        self.correct_answers = correct_answers
        self.selected_options = set()  # Track selected options
        self.is_multiple_answer = len(correct_answers) > 1
        
        # Add a label to indicate if multiple answers are allowed
        self.add_item(discord.ui.Button(
            label="Multiple answers allowed" if self.is_multiple_answer else "Select one answer",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0
        ))

    @discord.ui.button(label="A", style=discord.ButtonStyle.secondary, custom_id="mcq_A", row=1)
    async def button_a(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.secondary, custom_id="mcq_B", row=1)
    async def button_b(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "B")

    @discord.ui.button(label="C", style=discord.ButtonStyle.secondary, custom_id="mcq_C", row=1)
    async def button_c(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "C")

    @discord.ui.button(label="D", style=discord.ButtonStyle.secondary, custom_id="mcq_D", row=2)
    async def button_d(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "D")

    @discord.ui.button(label="E", style=discord.ButtonStyle.secondary, custom_id="mcq_E", row=2)
    async def button_e(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "E")

    @discord.ui.button(label="Not Sure", style=discord.ButtonStyle.danger, custom_id="mcq_not_sure", row=3)
    async def button_not_sure(self, interaction: discord.Interaction, button: Button):
        await self._handle_answer(interaction, "NOT SURE")

    @discord.ui.button(label="Submit Answer", style=discord.ButtonStyle.success, custom_id="mcq_submit", row=3)
    async def submit_button(self, interaction: discord.Interaction, button: Button):
        if not self.selected_options:
            await interaction.response.send_message("Please select at least one option before submitting.", ephemeral=True)
            return
            
        # Convert set to sorted list for consistent display
        selected_list = sorted(list(self.selected_options))
        
        # If only one answer is selected but multiple are allowed, that's fine
        # If only one answer is expected but multiple are selected, we'll still process it
        await self._handle_answer(interaction, selected_list)

    async def _toggle_option(self, interaction: discord.Interaction, button: Button, option: str):
        """Toggle selection of an option"""
        if option in self.selected_options:
            self.selected_options.remove(option)
            button.style = discord.ButtonStyle.secondary
        else:
            # If not multiple answer, clear previous selections
            if not self.is_multiple_answer:
                self.selected_options.clear()
                # Reset all buttons to secondary style
                for child in self.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id and child.custom_id.startswith("mcq_") and len(child.custom_id) == 5:
                        child.style = discord.ButtonStyle.secondary
            
            self.selected_options.add(option)
            button.style = discord.ButtonStyle.primary
            
        await interaction.response.edit_message(view=self)

    async def _handle_answer(self, interaction: discord.Interaction, answer):
        # Acknowledge the interaction immediately
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Get feedback for the answer
        response = await self.agent._handle_question_answer(answer, state)
        
        # Generate next question (but don't show it yet)
        next_question, next_correct_answers, _ = await self.agent._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        # Create feedback view with options
        view = FeedbackView(self.agent, next_question, next_correct_answers)
        
        # Send feedback with options but NOT the next question
        await interaction.followup.send(response, view=view)

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

class QuizMCQView(MCQView):
    def __init__(self, agent, question_text: str, correct_answers: list, quiz_state: QuizState):
        super().__init__(agent, question_text, correct_answers)
        self.quiz_state = quiz_state
        self.timer_button = discord.ui.Button(
            label=f"Time remaining: {quiz_state.format_time_remaining()}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0
        )
        self.add_item(self.timer_button)
        
        # Start the timer update task using the asyncio event loop
        self.timer_task = asyncio.get_event_loop().create_task(self.update_timer())
    
    async def update_timer(self):
        """Update the timer display every second"""
        try:
            while not self.quiz_state.is_finished() and not self.is_finished():
                # Wait for 1 second
                await asyncio.sleep(1)
                
                # Update the timer button label
                self.timer_button.label = f"Time remaining: {self.quiz_state.format_time_remaining()}"
                
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
                            response, view = await self.agent._grade_quiz(self.quiz_state)
                            
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
        response, view = await self.agent._grade_quiz(state["quiz_state"])
        
        # Show quiz results with the first question feedback
        await interaction.response.send_message(response, view=view)

    async def _handle_answer(self, interaction: discord.Interaction, answer):
        # Cancel the timer task for this view since we're moving to a new question
        if hasattr(self, 'timer_task') and not self.timer_task.done():
            self.timer_task.cancel()
            
        # Acknowledge the interaction immediately
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        quiz_state = state["quiz_state"]
        
        if quiz_state.is_finished():
            # Quiz is over, grade it and show the first question feedback
            response, view = await self.agent._grade_quiz(quiz_state)
            
            # Show quiz results with the first question feedback
            await interaction.followup.send(response, view=view)
            return
        
        # Record the answer
        quiz_state.user_answers.append(answer)
        
        # Generate next question
        question, correct_answers, concept = await self.agent._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        # Store the question
        quiz_state.questions.append((question, correct_answers, concept))
        
        # Create view for next question with updated timer
        next_view = QuizMCQView(self.agent, question, correct_answers, quiz_state)
        
        # Show time remaining with each question
        message = await interaction.followup.send(
            f"Next question:\n{question}",
            view=next_view
        )
        
        # Store the message reference in the view for timer updates
        next_view.message = message

class QuizDurationModal(discord.ui.Modal):
    def __init__(self, agent):
        super().__init__(title="Quiz Duration")
        self.agent = agent
        self.duration = discord.ui.TextInput(
            label="Quiz Duration (minutes)",
            placeholder="Enter a number between 1-60",
            required=True,
            max_length=2
        )
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration = int(self.duration.value)
            if duration < 1 or duration > 60:
                await interaction.response.send_message(
                    "Quiz duration must be between 1 and 60 minutes.",
                    ephemeral=True
                )
                return
                
            user_id = str(interaction.user.id)
            state = self.agent.conversation_state[user_id]
            
            # Initialize quiz state
            quiz_state = QuizState(duration, state["goal"])
            state["quiz_state"] = quiz_state
            state["state"] = UserState.IN_QUIZ
            
            # Generate first question
            question, correct_answers, concept = await self.agent._generate_question(
                state["goal"],
                state["question_history"],
                state["pdf_files"]
            )
            
            quiz_state.questions.append((question, correct_answers, concept))
            
            # Create MCQ view for quiz
            view = QuizMCQView(self.agent, question, correct_answers, quiz_state)
            
            # Format message with timer
            message = (
                f"Starting {duration}-minute quiz on {state['goal']}\n"
                f"Time remaining: {quiz_state.format_time_remaining()}\n\n"
                f"{question}"
            )
            
            # Send the message
            await interaction.response.send_message(message, view=view)
            
            # We can't directly store the message reference here since interaction.response.send_message
            # doesn't return the message object. The timer will still work but won't update the UI.
            
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number for the quiz duration.",
                ephemeral=True
            )

class PracticeMCQView(MCQView):
    def __init__(self, agent, question_text: str, correct_answers: list):
        super().__init__(agent, question_text, correct_answers)
    
    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger, custom_id="end_session")
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

class StudyAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.conversation_state = {}  # Track state per user
        self.command_patterns = {
            Command.ANSWER: r'!answer\s+([A-Ea-e](?:[,\s]+[A-Ea-e])*|not sure)',
            Command.ASK: r'!ask\s+(.*)',
            Command.GOAL: r'!goal\s+(.*)',
            Command.UPLOAD: r'!upload',
            Command.QUIZ: r'!quiz\s+(\d+)'
        }
        self.quiz_states = {}  # Store quiz states per user

    async def _generate_question(self, goal: str, question_history=None, pdf_files=None):
        """Generate a multiple choice question about the given goal."""
        # Base prompt
        content = f"Generate a challenging multiple choice question about {goal}."
        
        # Track covered concepts and identify weak areas from question history
        covered_concepts = set()
        weak_concepts = {}
        
        if question_history:
            for q in question_history:
                if 'concept' in q and q['concept'] != 'Unknown':
                    covered_concepts.add(q['concept'])
                    # Track concepts the user got wrong
                    if not q.get('is_correct', True):
                        weak_concepts[q['concept']] = weak_concepts.get(q['concept'], 0) + 1
        
        # Determine question strategy based on history
        if question_history and len(question_history) > 0:
            # Every third question should explore a new aspect of the goal
            if len(question_history) % 3 == 0:
                content = f"Generate a challenging multiple choice question for the following goal: {goal}. The question should explore an aspect or concept NOT covered in previous questions. Focus on comprehensive coverage of the goal."
            # Otherwise, focus on weak areas if available
            elif weak_concepts:
                # Sort weak concepts by frequency (most frequently wrong first)
                sorted_weak_concepts = sorted(weak_concepts.items(), key=lambda x: x[1], reverse=True)
                weak_concepts_list = [concept for concept, count in sorted_weak_concepts[:3]]
                content = f"Generate a challenging multiple choice question for the following goal: {goal}. The question should focus specifically on these concepts: {weak_concepts_list}. These are areas where the student has shown weakness, so it's important to test them on these concepts."
            # If no weak areas or it's not time for a new concept, use general question
            else:
                content = f"Generate a challenging multiple choice question for the following goal: {goal}. The question should test an important concept within this subject."
        
        # Add context about document usage and comprehensive coverage
        if pdf_files and len(pdf_files) > 0:
            content += f"\n\nIMPORTANT: Base your question on content from the uploaded documents. These may be lecture slides, past exams, or textbook content. Extract specific concepts, examples, or problems from these materials to create an authentic question."
            
            # Add information about the number of documents
            content += f"\n\nThe student has uploaded {len(pdf_files)} document(s). Use these as your primary source for creating questions."
            
            # Emphasize comprehensive coverage
            content += f"\n\nEnsure you cover ALL parts of {goal} mentioned in the documents. If you've already covered some concepts in previous questions, try to explore different aspects of the goal."
            
            # Emphasize self-contained questions
            content += f"""\n\n⚠️ CRITICAL REQUIREMENT: CREATE FULLY SELF-CONTAINED QUESTIONS ⚠️
1. Questions MUST be completely self-contained in text form
2. DO NOT create questions that reference figures, images, diagrams, or visual elements from the documents
3. If you need to reference content that appears in a figure or diagram, fully describe it in text within your question
4. The user should NEVER need to look at the original document to understand or answer the question
5. If a concept relies heavily on visual elements that cannot be adequately described in text, choose a different concept to test
6. NEVER say "refer to figure X" or "as shown in the diagram" or similar phrases"""
        
        # Add information about covered concepts
        if covered_concepts:
            content += f"\n\nConcepts already covered in previous questions: {list(covered_concepts)}."
        
        # Add information about weak concepts
        if weak_concepts:
            content += f"\n\nThe student has struggled with these concepts (consider focusing on them): {list(weak_concepts.keys())}."
        
        # Add question history context if available
        if question_history and len(question_history) > 0:
            # Extract concepts the user got wrong
            incorrect_questions = [q for q in question_history if not q.get("is_correct", False)]
            if incorrect_questions:
                content += f"\n\nThe student has struggled with these previous questions (focus on similar concepts):"
                for i, q in enumerate(incorrect_questions[-3:]):  # Show last 3 incorrect questions
                    # Handle both old format (correct_answer) and new format (correct_answers)
                    if "correct_answer" in q:
                        correct_ans_display = q["correct_answer"]
                    else:
                        correct_ans_display = ", ".join(q["correct_answers"]) if isinstance(q["correct_answers"], list) else q["correct_answers"]
                    
                    content += f"\n{i+1}. Question: {q['question']}\n   User answered: {q['user_answer']}\n   Correct answer: {correct_ans_display}"
            
            # Avoid repeating questions
            content += "\n\nAvoid creating questions that are too similar to these previous questions:"
            for i, q in enumerate(question_history[-5:]):  # Last 5 questions
                content += f"\n{i+1}. {q['question']}"

        print("generating question")
        
        contents = []
        
        # Add PDF files to the contents if available
        if pdf_files:
            gemini_files = await self._get_gemini_files(pdf_files)
            for gemini_file in gemini_files:
                contents.append(gemini_file)
        
        # Add the text content
        contents.append(content)
        
        response = self.client.models.generate_content(
            model=MODEL,
            contents=contents,
            config={
                "max_output_tokens": 500,  # Limit response size
                "temperature": 0.7,
                'response_mime_type': 'application/json',
                'response_schema': {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The multiple choice question text. Must be completely self-contained and not reference any figures, images, or diagrams that aren't fully described in text."
                        },
                        "options": {
                            "type": "object",
                            "properties": {
                                "A": {
                                    "type": "string",
                                    "description": "The first multiple choice option"
                                },
                                "B": {
                                    "type": "string",
                                    "description": "The second multiple choice option"
                                },
                                "C": {
                                    "type": "string",
                                    "description": "The third multiple choice option"
                                },
                                "D": {
                                    "type": "string",
                                    "description": "The fourth multiple choice option"
                                },
                                "E": {
                                    "type": "string",
                                    "description": "The fifth multiple choice option (optional)"
                                }
                            },
                            "required": ["A", "B", "C", "D"]
                        },
                        "correct_answers": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["A", "B", "C", "D", "E"]
                            },
                            "description": "The correct answer(s). Can be a single letter or multiple letters if more than one answer is correct."
                        },
                        "multiple_answers_allowed": {
                            "type": "boolean",
                            "description": "Whether this question allows multiple correct answers"
                        },
                        "concept_tested": {
                            "type": "string",
                            "description": "The specific concept or knowledge area being tested in this question"
                        },
                        "source": {
                            "type": "string",
                            "description": "If from an uploaded document, mention which document or slide this question is based on"
                        }
                    },
                    "required": ["question", "options", "correct_answers", "multiple_answers_allowed", "concept_tested"]
                }
            }
        )

        print("response", response.text)

        question_data = json.loads(response.text)
        
        # Check if this is a multiple-answer question
        is_multiple_answer = question_data.get('multiple_answers_allowed', False) or len(question_data['correct_answers']) > 1
        
        # Add instruction for multiple answers if applicable
        multiple_answer_instruction = ""
        if is_multiple_answer:
            multiple_answer_instruction = " (Select ALL that apply)"
        
        # Format the question text with options
        formatted_question = (
            f"{question_data['question']}{multiple_answer_instruction}\n\n"
            f"A) {question_data['options']['A']}\n"
            f"B) {question_data['options']['B']}\n"
            f"C) {question_data['options']['C']}\n"
            f"D) {question_data['options']['D']}"
        )
        
        # Add option E if it exists
        if 'E' in question_data['options']:
            formatted_question += f"\nE) {question_data['options']['E']}"
        
        # Add source information if available
        if 'source' in question_data and question_data['source']:
            formatted_question += f"\n\n(Source: {question_data['source']})"
        
        # Extract the concept being tested
        concept_tested = question_data.get('concept_tested', 'Unknown')
        
        return formatted_question, question_data['correct_answers'], concept_tested

    async def _evaluate_answer(self, question: str, user_answer: str, correct_answers: list, pdf_files=None):
        """Evaluate the user's answer and return feedback."""
        # Extract the question text and options
        question_parts = question.split("\n\n")
        question_text = question_parts[0]
        options = question_parts[1].split("\n")
        
        # Format correct answers for display
        if len(correct_answers) == 1:
            correct_answers_display = correct_answers[0]
        else:
            correct_answers_display = ", ".join(sorted(correct_answers))
        
        # Find the correct option text for all correct answers
        correct_options_text = []
        for correct_answer in correct_answers:
            for option in options:
                if option.startswith(f"{correct_answer})"):
                    correct_options_text.append(f"{correct_answer}) {option[3:].strip()}")
                    break
        
        # Find the user's option text(s)
        user_options_text = []
        user_answers = user_answer.split(", ")
        for user_ans in user_answers:
            for option in options:
                if option.startswith(f"{user_ans})"):
                    user_options_text.append(f"{user_ans}) {option[3:].strip()}")
                    break
        
        # Prepare the content for evaluation
        content = f"""Question: {question_text}
Student answered: {user_answer} ({"; ".join(user_options_text)})
Correct answer{'s' if len(correct_answers) > 1 else ''}: {correct_answers_display}
Correct option{'s' if len(correct_answers) > 1 else ''}:
{chr(10).join([f"- {text}" for text in correct_options_text])}

Evaluate the student's answer and provide detailed feedback."""

        try:
            print("evaluating answer")
            
            contents = []
            
            # Add PDF files to the contents if available
            if pdf_files:
                gemini_files = await self._get_gemini_files(pdf_files)
                for gemini_file in gemini_files:
                    contents.append(gemini_file)
            
            # Add the text content
            contents.append(content)
            
            response = self.client.models.generate_content(
                model=MODEL,
                contents=contents,
                config={
                    "max_output_tokens": 500,  # Limit response size
                    "temperature": 0.7,
                    'response_mime_type': 'application/json',
                    'response_schema': {
                        "type": "object",
                        "properties": {
                            "correct": {
                                "type": "boolean",
                                "description": "Whether the student's answer was correct"
                            },
                            "concept": {
                                "type": "string",
                                "description": "The specific concept being tested (be precise and specific, 1-5 words)"
                            },
                            "feedback": {
                                "type": "string", 
                                "description": "Detailed explanation and feedback that helps the student understand why their answer was right or wrong"
                            },
                            "improvement_suggestion": {
                                "type": "string",
                                "description": "A specific suggestion to help the student improve their understanding of this concept"
                            }
                        },
                        "required": ["correct", "concept", "feedback", "improvement_suggestion"]
                    }
                }
            )
            
            result = json.loads(response.text)
            
            # Combine feedback with improvement suggestion
            result["feedback"] = f"{result['feedback']}\n\n{result['improvement_suggestion']}"
            
            return result
        except Exception as e:
            print(f"Error evaluating answer: {e}")
            print(f"Response: {response if 'response' in locals() else 'No response'}")
            return None

    def _initialize_state(self, user_id: str):
        """Initialize conversation state for a new user."""
        self.user_id = user_id

        # Check for existing PDF files in the user's directory
        pdf_files = []
        user_dir = f"user_files/{user_id}"
        if os.path.exists(user_dir):
            for filename in os.listdir(user_dir):
                if filename.lower().endswith('.pdf'):
                    pdf_files.append(f"{user_dir}/{filename}")
        
        self.conversation_state[user_id] = {
            "state": UserState.INITIAL,
            "goal": None,
            "question": None,
            "correct_answers": [],
            "question_history": [],  # Track previous questions and answers
            "pdf_files": pdf_files   # Store paths to saved PDF files
        }
        
        # Log the initialization
        if pdf_files:
            print(f"Initialized state for user {user_id} with {len(pdf_files)} existing PDF files")
        else:
            print(f"Initialized state for user {user_id}")
        
        # Customize the message based on whether there are existing PDFs
        pdf_message = ""
        if pdf_files:
            pdf_message = f"\n\nI found {len(pdf_files)} previously uploaded PDF document(s). You can use `!goal [learning goal]` to start learning from these materials."
            
        message = "Welcome! Let's start by setting a learning goal."
        
        # Create a view with just the goal button
        view = InitialView(self)
        
        return message, view

    async def _save_attachment(self, attachment, user_id):
        """Save an attachment to disk and return the file path."""
        # Create directory for user if it doesn't exist
        user_dir = f"user_files/{user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Generate a filename based on the attachment name
        filename = attachment.filename
        filepath = f"{user_dir}/{filename}"
        
        # Download and save the file
        await attachment.save(filepath)
        return filepath
    
    async def _get_file_hash(self, filepath):
        """Calculate SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            # Read and update hash in chunks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    async def _get_gemini_files(self, file_paths):
        """Upload files to Gemini API and return file objects."""
        gemini_files = []
        
        # Create user directory if it doesn't exist
        user_dir = f"user_files/{self.user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Path to the file mapping JSON
        mapping_file = f"{user_dir}/file_mapping.json"
        
        # Load existing mapping if it exists
        file_mapping = {}
        if os.path.exists(mapping_file):
            with open(mapping_file, 'r') as f:
                file_mapping = json.load(f)
        
        # Process each file
        for filepath in file_paths:
            # Calculate file hash
            file_hash = await self._get_file_hash(filepath)
            
            # Check if file is already uploaded
            if file_hash in file_mapping:
                print(f"Using existing Gemini file for {filepath}")
                gemini_files.append(file_mapping[file_hash])
            else:
                # Upload file to Gemini
                print(f"Uploading {filepath} to Gemini")
                gemini_file = self.client.files.upload(file=filepath)
                
                # Store the mapping
                file_mapping[file_hash] = gemini_file.name
                gemini_files.append(gemini_file.name)
        
        # Save updated mapping
        with open(mapping_file, 'w') as f:
            json.dump(file_mapping, f)

        return gemini_files
        
    async def _add_pdf_contents(self, contents, pdf_files):
        """Helper method to add PDF files to contents list"""
        if pdf_files:
            for pdf_path in pdf_files:
                contents.append(
                    types.Part.from_bytes(
                        data=pathlib.Path(pdf_path).read_bytes(),
                        mime_type='application/pdf',
                    ))
        return contents
    
    def _parse_command(self, message_content):
        """Parse the message to identify commands and their arguments"""
        for command, pattern in self.command_patterns.items():
            match = re.match(pattern, message_content, re.IGNORECASE)
            if match:
                if command == Command.ANSWER:
                    answer_text = match.group(1).strip().upper()
                    # Handle "not sure" case
                    if re.match(r'NOT SURE', answer_text, re.IGNORECASE):
                        return command, "NOT SURE"
                    
                    # Handle multiple answers (e.g., "A,B,C" or "A B C" or "A, B, C")
                    if ',' in answer_text or ' ' in answer_text:
                        # Split by comma or space and clean up
                        answers = re.split(r'[,\s]+', answer_text)
                        # Filter out empty strings and sort
                        answers = sorted([a.strip() for a in answers if a.strip()])
                        # Validate each answer is a valid option
                        valid_answers = [a for a in answers if re.match(r'^[A-E]$', a)]
                        if valid_answers:
                            return command, valid_answers
                        return command, "INVALID"
                    
                    # Single answer
                    if re.match(r'^[A-E]$', answer_text):
                        return command, answer_text
                    return command, "INVALID"
                    
                elif command in [Command.ASK, Command.GOAL, Command.QUIZ]:
                    return command, match.group(1)
                else:  # Command.UPLOAD
                    return command, None
        return Command.NONE, message_content
        
    async def _handle_question(self, question, goal, pdf_files=None):
        """Handle a question from the user about the goal"""
        content = f"The student is learning about {goal} and has asked: {question}\n\nPlease answer this question concisely within 2000 characters."
        print("responding to question")
        
        contents = []
        if pdf_files:
            gemini_files = await self._get_gemini_files(pdf_files)
            for gemini_file in gemini_files:
                contents.append(gemini_file)
        contents.append(content)
        
        response = self.client.models.generate_content(
            model=MODEL,
            contents=contents,
            config={
                "max_output_tokens": 250,  # Limit response to fit in Discord's message limit
                "temperature": 0.7
            }
        )
        
        return response.text, ResponseView(self)  # Return both response and view
        
    async def _handle_goal_switch(self, state, new_goal):
        """Handle a goal switch from the user"""
        state["state"] = UserState.INITIAL
        state["goal"] = new_goal if new_goal else None
        
        if state["goal"]:
            state["state"] = UserState.ASKING_QUESTION
            question, correct_answers, _ = await self._generate_question(
                state["goal"], 
                question_history=None, 
                pdf_files=state["pdf_files"]
            )
            state["question"] = question
            state["correct_answers"] = correct_answers
            
            # Create MCQ view
            view = MCQView(self, question, correct_answers)
            
            # Return both the question and view
            return question, view
        return "What learning goal would you like to set for this session? Use `!goal [learning goal]`"
        
    async def _handle_initial_state(self, message_content, state):
        """Handle the initial state when user is providing a goal"""
        state["goal"] = message_content
        state["state"] = UserState.ASKING_QUESTION
        
        state["question"], state["correct_answers"], _ = await self._generate_question(message_content, question_history=None, pdf_files=state["pdf_files"])
        return state["question"]
        
    async def _handle_question_answer(self, user_answer, state, pdf_files=None):
        """Handle an answer from the user"""
        # If we're in a quiz, handle differently
        if state["state"] == UserState.IN_QUIZ:
            return await self._handle_quiz_answer(user_answer, state)
        
        # Get PDF files if not provided
        if pdf_files is None:
            pdf_files = state.get("pdf_files", [])
        
        # Handle "not sure" answer
        if user_answer == "NOT SURE":
            # Generate feedback for skipping
            eval_response = await self._evaluate_answer(
                state["question"], 
                "NOT SURE", 
                state["correct_answers"],
                pdf_files
            )
            
            if eval_response is None:
                # If evaluation failed, generate a new question
                state["question"], state["correct_answers"], _ = await self._generate_question(
                    state["goal"], 
                    question_history=state["question_history"],
                    pdf_files=pdf_files
                )
                return "I understand you're not sure about this one. Let's try a different question."
            
            # Add to history
            state["question_history"].append({
                "question": state["question"],
                "user_answer": "NOT SURE",
                "correct_answers": state["correct_answers"],
                "concept": eval_response["concept"],
                "is_correct": False
            })
            
            # Generate next question
            state["question"], state["correct_answers"], _ = await self._generate_question(
                state["goal"], 
                state["question_history"],
                state["pdf_files"]
            )
            
            # Prepare response with feedback only (no next question)
            feedback = eval_response["feedback"]
            
            # Check if feedback is too long for Discord
            if len(feedback) > 1900:  # Leave some buffer
                feedback = feedback[:1900] + "... (feedback truncated)"
            
            return feedback
        
        # Handle regular answer
        correct_answers = state["correct_answers"]
        
        # Check if this is a multiple-answer question
        is_multiple_answer = len(correct_answers) > 1
        
        # Convert user_answer to list if it's a string (single answer)
        user_answers = user_answer if isinstance(user_answer, list) else [user_answer]
        
        # Prepare user answer display for evaluation
        if len(user_answers) == 1:
            user_answer_display = user_answers[0]
        else:
            user_answer_display = ", ".join(sorted(user_answers))
        
        eval_response = await self._evaluate_answer(state["question"], user_answer_display, correct_answers, pdf_files=pdf_files)
        
        if eval_response is None:
            state["question"], state["correct_answers"], _ = await self._generate_question(state["goal"], question_history=state["question_history"], pdf_files=pdf_files)
            return "Sorry, I couldn't grade your response."
        
        is_correct = eval_response["correct"]
        concept = eval_response["concept"]
        feedback = eval_response["feedback"]
        
        # Update history
        state["question_history"].append({
            "question": state["question"],
            "user_answer": user_answer_display,
            "correct_answers": correct_answers,
            "concept": concept,
            "is_correct": is_correct
        })
        
        # Generate next question focusing on weak areas (but don't include it in the response)
        state["question"], state["correct_answers"], _ = await self._generate_question(
            state["goal"], 
            state["question_history"],
            state["pdf_files"]
        )
        
        # Return only the feedback
        if len(feedback) > 1900:  # Leave some buffer
            feedback = feedback[:1900] + "... (feedback truncated)"
        
        return feedback

    async def _handle_pdf_attachments(self, message):
        """Handle PDF attachments from the user"""
        user_id = str(message.author.id)
        state = self.conversation_state[user_id]
        pdf_files = []
        
        for attachment in message.attachments:
            if attachment.filename.lower().endswith('.pdf'):
                filepath = await self._save_attachment(attachment, user_id)
                state["pdf_files"].append(filepath)
                pdf_files.append(filepath)
        
        if pdf_files:
            message_text = f"I've received {len(pdf_files)} PDF document(s). I'll use these to help with your learning.\n\nPlease select your study mode:"
            view = StudyModeView(self)
            await message.channel.send(message_text, view=view)
            return None
        else:
            return "I can only process PDF files at the moment. Please send PDF documents."

    async def _grade_quiz(self, quiz_state):
        """Grade a completed quiz and provide feedback for the first question"""
        if not quiz_state or not quiz_state.questions:
            return "No quiz data available to grade."
        
        # Calculate overall score
        total_questions = len(quiz_state.user_answers)
        correct_count = 0
        
        # Grade each question
        for i, (user_answer, (question, correct_answers, concept)) in enumerate(
            zip(quiz_state.user_answers, quiz_state.questions)
        ):
            # Convert user_answer to list if it's a string (single answer)
            user_answers = user_answer if isinstance(user_answer, list) else [user_answer]
            
            # Check if answer is correct (all required answers are present and no incorrect ones)
            is_correct = set(user_answers) == set(correct_answers)
            
            if is_correct:
                correct_count += 1

        # Calculate score
        score_percent = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        # Create initial message with score
        message = [
            f"**Quiz Completed!**",
            f"Topic: {quiz_state.goal}",
            f"Score: {correct_count}/{total_questions} ({score_percent:.1f}%)",
            f"",
            f"Let's review your answers one by one:",
            f""
        ]
        
        # Create the results view starting with the first question
        view = QuizResultsView(self, quiz_state, 0)
        
        # Add the feedback for the first question
        message.append(view._get_current_feedback())
        
        return "\n".join(message), view

    async def _handle_quiz_answer(self, user_answer: str, state: dict):
        """Handle an answer during a quiz"""
        quiz_state = state["quiz_state"]
        
        if quiz_state.is_finished():
            # Quiz is over, grade it
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            return await self._grade_quiz(quiz_state)
        
        # Record the answer
        quiz_state.user_answers.append(user_answer)
        
        # Generate next question
        question, correct_answers, concept = await self._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        # Store the question
        quiz_state.questions.append((question, correct_answers, concept))
        
        return (
            f"Answer recorded. Time remaining: {quiz_state.format_time_remaining()}\n\n"
            f"Next question:\n{question}"
        )

    async def _handle_quiz_command(self, duration_minutes: int, state: dict):
        """Handle the quiz command"""
        if not state["goal"]:
            return "Please set a learning goal first using `!goal [learning goal]`"
        
        if duration_minutes < 1 or duration_minutes > 60:
            return "Quiz duration must be between 1 and 60 minutes."
        
        # Initialize quiz state
        quiz_state = QuizState(duration_minutes, state["goal"])
        state["quiz_state"] = quiz_state
        state["state"] = UserState.IN_QUIZ
        
        # Generate first question
        question, correct_answers, concept = await self._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        quiz_state.questions.append((question, correct_answers, concept))
        
        # Create MCQ view for quiz
        view = QuizMCQView(self, question, correct_answers, quiz_state)
        
        message = (
            f"Starting {duration_minutes}-minute quiz on {state['goal']}\n"
            f"Time remaining: {quiz_state.format_time_remaining()}\n\n"
            f"{question}"
        )
        
        return message, view

    async def run(self, message: discord.Message):
        user_id = str(message.author.id)
        
        if user_id not in self.conversation_state:
            message_text, view = self._initialize_state(user_id)
            await message.channel.send(message_text, view=view)
            return None
        
        state = self.conversation_state[user_id]
        
        # Parse the command from the message
        command, argument = self._parse_command(message.content)
        
        # Handle attachments with !upload command
        if command == Command.UPLOAD or (command == Command.NONE and message.attachments):
            return await self._handle_pdf_attachments(message)
        
        # Handle commands based on type
        if command == Command.GOAL:
            response, view = await self._handle_goal_switch(state, argument)
            await message.channel.send(response, view=view)
            return None
            
        if command == Command.ASK:
            if state["goal"]:
                return await self._handle_question(argument, state["goal"], state["pdf_files"])
            else:
                return "Please set a goal first using `!goal [learning goal]`"
        
        # Handle quiz command
        if command == Command.QUIZ:
            try:
                duration = int(argument)
                response, view = await self._handle_quiz_command(duration, state)
                await message.channel.send(response, view=view)
                return None
            except ValueError:
                return "Invalid quiz duration. Please specify a number of minutes between 1 and 60."
        
        # Handle answers during quiz
        if state["state"] == UserState.IN_QUIZ:
            quiz_state = state["quiz_state"]
            
            if command == Command.ANSWER:
                if argument == "INVALID":
                    return "Invalid answer format. Please use `!answer [letter]` (e.g., `!answer A`)."
                
                result = await self._handle_quiz_answer(argument, state)
                if result:  # Quiz is finished
                    return result
                    
                # Check if time expired while processing
                if quiz_state.is_finished():
                    state["state"] = UserState.ASKING_QUESTION
                    state["quiz_state"] = None
                    return await self._grade_quiz(quiz_state)
                    
                return result
            else:
                return "You're currently in a quiz. Use `!answer [letter]` to submit your answer."
            
        # handles answers not part of a quiz
        if command == Command.ANSWER:
            if state["state"] == UserState.ASKING_QUESTION:
                return await self._handle_question_answer(argument, state)
            else:
                return "There's no active question to answer. Use `!goal [learning goal]` to start a new goal."
        
        # Handle regular messages (no command)
        if state["state"] == UserState.INITIAL:
            return await self._handle_initial_state(message.content, state)
        elif state["state"] == UserState.ASKING_QUESTION:
            # Treat as a regular message - suggest using commands
            return "I didn't recognize that as a command. Please use `!answer [A/B/C/D/E]` or `!answer not sure` to answer the question, `!ask [question]` to ask a question, or `!goal [subject]` to switch topics."

    async def _generate_quiz_summary(self, quiz_state):
        """Generate an overall summary of the quiz results"""
        if not quiz_state or not quiz_state.questions:
            return "No quiz data available to summarize."
        
        total_questions = len(quiz_state.user_answers)
        correct_count = 0
        incorrect_questions = []
        
        # Grade each question
        for i, (user_answer, (question, correct_answers, concept)) in enumerate(
            zip(quiz_state.user_answers, quiz_state.questions)
        ):
            # Convert user_answer to list if it's a string (single answer)
            user_answers = user_answer if isinstance(user_answer, list) else [user_answer]
            
            # Check if answer is correct (all required answers are present and no incorrect ones)
            is_correct = set(user_answers) == set(correct_answers)
            
            if is_correct:
                correct_count += 1
            else:
                incorrect_questions.append((i+1, question, user_answers, correct_answers, concept))
        
        # Calculate score
        score_percent = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        # Group incorrect questions by concept
        concept_errors = {}
        for q_num, question, user_answer, correct_answer, concept in incorrect_questions:
            if concept not in concept_errors:
                concept_errors[concept] = []
            concept_errors[concept].append(q_num)
        
        # Build response
        response = [
            f"**Quiz Results Summary**",
            f"Topic: {quiz_state.goal}",
            f"Score: {correct_count}/{total_questions} ({score_percent:.1f}%)",
            f"Time: {quiz_state.duration_minutes} minutes",
            ""
        ]
        
        # Add feedback on concepts that need improvement
        if concept_errors:
            response.append("**Areas to Review:**")
            for concept, question_nums in concept_errors.items():
                q_str = ", ".join([f"#{num}" for num in question_nums])
                response.append(f"• {concept} (Questions {q_str})")
            response.append("")
        
        # Generate personalized feedback using Gemini
        try:
            # Create a prompt for Gemini
            concepts_tested = [concept for _, _, concept in quiz_state.questions]
            incorrect_concepts = list(concept_errors.keys())
            
            prompt = (
                f"The student has completed a quiz on '{quiz_state.goal}'.\n\n"
                f"They answered {total_questions} questions with {correct_count} correct and {total_questions - correct_count} incorrect.\n\n"
                f"Concepts tested: {', '.join(set(concepts_tested))}\n"
                f"Concepts they struggled with: {', '.join(incorrect_concepts)}\n\n"
                f"Please provide a brief, encouraging summary (2-3 sentences) of their performance and 1-2 specific suggestions for what to focus on next."
            )
            
            ai_response = self.client.models.generate_content(
                model=MODEL,
                contents=[prompt],
                config={
                    "max_output_tokens": 200,
                    "temperature": 0.7
                }
            )
            
            response.append(f"**AI Feedback:**\n{ai_response.text}")
        except Exception as e:
            print(f"Error generating AI feedback: {e}")
        
        return "\n".join(response)

class QuizResultsView(View):
    def __init__(self, agent, quiz_state, current_index=0):
        super().__init__(timeout=None)
        self.agent = agent
        self.quiz_state = quiz_state
        self.current_index = current_index
        self.total_questions = len(quiz_state.user_answers)
        
        # Disable next button if we're on the last question
        if self.current_index >= self.total_questions - 1:
            self.next_button.disabled = True
            
        # Disable previous button if we're on the first question
        if self.current_index <= 0:
            self.previous_button.disabled = True
    
    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.current_index > 0:
            # Show the previous question's feedback
            new_view = QuizResultsView(self.agent, self.quiz_state, self.current_index - 1)
            await interaction.response.edit_message(content=self._get_current_feedback(), view=new_view)
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_index < self.total_questions - 1:
            # Show the next question's feedback
            new_view = QuizResultsView(self.agent, self.quiz_state, self.current_index + 1)
            await interaction.response.edit_message(content=self._get_current_feedback(), view=new_view)
    
    @discord.ui.button(label="Summary", style=discord.ButtonStyle.success)
    async def summary_button(self, interaction: discord.Interaction, button: Button):
        # Generate and show the overall summary
        summary = await self.agent._generate_quiz_summary(self.quiz_state)
        await interaction.response.edit_message(content=summary, view=None)
        
        # Show the initial view for next steps
        await interaction.followup.send("What would you like to do next?", view=InitialView(self.agent))
    
    @discord.ui.button(label="End Quiz", style=discord.ButtonStyle.danger)
    async def end_button(self, interaction: discord.Interaction, button: Button):
        # Reset user state but keep PDF files
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
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
        
        # Show the initial view
        await interaction.response.edit_message(content="Quiz ended.", view=None)
        await interaction.followup.send("What would you like to do next?", view=InitialView(self.agent))
    
    def _get_current_feedback(self):
        """Get feedback for the current question"""
        if self.current_index >= len(self.quiz_state.user_answers):
            return "No more questions to review."
            
        # Get the current question data
        user_answer = self.quiz_state.user_answers[self.current_index]
        question, correct_answers, concept = self.quiz_state.questions[self.current_index]
        
        # Convert user_answer to list if it's a string (single answer)
        user_answers = user_answer if isinstance(user_answer, list) else [user_answer]
        
        # Check if answer is correct
        is_correct = set(user_answers) == set(correct_answers)
        
        # Format the user's answer for display
        if len(user_answers) == 1:
            user_answer_display = user_answers[0]
        else:
            user_answer_display = ", ".join(sorted(user_answers))
            
        # Format the correct answer for display
        if len(correct_answers) == 1:
            correct_answer_display = correct_answers[0]
        else:
            correct_answer_display = ", ".join(sorted(correct_answers))
        
        # Build the feedback message
        feedback = [
            f"**Question {self.current_index + 1} of {self.total_questions}**",
            f"",
            f"{question}",
            f"",
            f"Your answer: {user_answer_display}",
            f"Correct answer: {correct_answer_display}",
            f"Result: {'✅ Correct' if is_correct else '❌ Incorrect'}",
            f"Concept: {concept}",
            f"",
        ]
        
        return "\n".join(feedback)