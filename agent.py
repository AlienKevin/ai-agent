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
    END = "end"  # Add this new command
    NONE = "none"

class InitialView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="Set Learning Goal", style=discord.ButtonStyle.primary)
    async def goal_button(self, interaction: discord.Interaction, button: Button):
        modal = GoalModal(self.agent)
        await interaction.response.send_modal(modal)
        
    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger)
    async def end_session_button(self, interaction: discord.Interaction, button: Button):
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
        
        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer(thinking=True)
        
        # Generate first question
        question, correct_answers = await self.agent._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        state["question"] = question
        state["correct_answers"] = correct_answers
        state["state"] = UserState.ASKING_QUESTION
        
        # Create MCQ view with end session button
        view = PracticeMCQView(self.agent, question, correct_answers)
        
        # Follow up with the actual message after the question is generated
        await interaction.followup.send(question, view=view)

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
    
    @discord.ui.button(label="Ask Follow-up Question", style=discord.ButtonStyle.secondary)
    async def follow_up_button(self, interaction: discord.Interaction, button: Button):
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
            view = FeedbackView(self.agent, self.next_question, self.next_correct_answers)
            
            # Send the response
            await interaction.followup.send(response_text, view=view)
            
        except Exception as e:
            print(f"Error in follow-up question: {e}")
            await interaction.followup.send(
                f"I'm sorry, I encountered an error processing your follow-up question: {str(e)}. Please try again with a different question.",
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
        """Base implementation to be overridden by subclasses"""
        await interaction.response.send_message(
            "This method should be overridden by subclasses.",
            ephemeral=True
        )

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
        
        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        quiz_state = state["quiz_state"]
        
        # Record the answer
        quiz_state.user_answers.append(answer)
        
        # Check if quiz is finished
        if quiz_state.is_finished():
            # Quiz is over, grade it
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            response, view = await self.agent._grade_quiz(quiz_state)
            await self._send_long_message(interaction, response, view)
            return
        
        # Generate next question
        try:
            # Get the next question
            question, correct_answers = await self.agent._generate_question(
                state["goal"],
                state["question_history"],
                state["pdf_files"]
            )
            
            # Store the question (without concept since it's not returned)
            quiz_state.questions.append((question, correct_answers))
            
            # Create view for next question with updated timer
            next_view = QuizMCQView(self.agent, question, correct_answers, quiz_state)
            
            # Show time remaining with each question
            message = (
                f"Answer recorded. Time remaining: {quiz_state.format_time_remaining()}\n\n"
                f"Next question:\n{question}"
            )
            
            # Handle long messages
            await self._send_long_message(interaction, message, next_view)
            
        except Exception as e:
            print(f"Error generating next question: {e}")
            # End the quiz early if there's an error
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            await interaction.followup.send(
                "I encountered an error generating the next question. The quiz has been ended.",
                view=InitialView(self.agent)
            )

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
            # Acknowledge the interaction immediately to prevent timeout
            await interaction.response.defer()
            
            duration = int(self.duration.value)
            if duration < 1 or duration > 60:
                await interaction.followup.send(
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
            try:
                question, correct_answers = await self.agent._generate_question(
                    state["goal"],
                    state["question_history"],
                    state["pdf_files"]
                )
                
                quiz_state.questions.append((question, correct_answers))
                
                # Create MCQ view for quiz
                view = QuizMCQView(self.agent, question, correct_answers, quiz_state)
                
                # Format message with timer
                message = (
                    f"Starting {duration}-minute quiz on {state['goal']}\n"
                    f"Time remaining: {quiz_state.format_time_remaining()}\n\n"
                    f"{question}"
                )
                
                # Send the message using followup since we deferred
                await interaction.followup.send(message, view=view)
                
            except Exception as e:
                print(f"Error in quiz generation: {e}")
                # Reset state
                state["state"] = UserState.ASKING_QUESTION
                state["quiz_state"] = None
                
                await interaction.followup.send(
                    "I encountered an error starting your quiz. Please try again."
                )
                
        except ValueError:
            # If we haven't responded yet, use response
            try:
                await interaction.response.send_message(
                    "Please enter a valid number for the quiz duration.",
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                # If we've already responded, use followup
                await interaction.followup.send(
                    "Please enter a valid number for the quiz duration.",
                    ephemeral=True
                )

class PracticeMCQView(MCQView):
    def __init__(self, agent, question, correct_answers):
        super().__init__(agent, question, correct_answers)
        
    async def _handle_answer(self, interaction: discord.Interaction, answer):
        """Handle a practice question answer (not part of a quiz)"""
        # Acknowledge the interaction immediately to prevent timeout
        await interaction.response.defer()
        
        user_id = str(interaction.user.id)
        state = self.agent.conversation_state[user_id]
        
        # Get feedback for the answer
        response = await self.agent._handle_question_answer(answer, state)
        
        # Generate next question (but don't show it yet)
        next_question, correct_answers = await self.agent._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        # Create feedback view with options
        view = FeedbackView(self.agent, next_question, correct_answers)
        
        # Send feedback with options but NOT the next question
        await interaction.followup.send(response, view=view)

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
        
        if question_history and len(question_history) > 0:
            content += f"\n\nThe student has already answered these questions:"
            for i, q in enumerate(question_history):
                content += f"\n{i+1}. Question: {q['question']}\n   User answered: {q['user_answer']}\n   Correct answer: {q['correct_answers']}"
        
        # Add context about document usage and comprehensive coverage
        if pdf_files and len(pdf_files) > 0:
            content += f"\n\nBase your question on content from the uploaded documents. These may be lecture slides, past exams, or textbook content. Extract specific concepts, examples, or problems from these materials to create an authentic question. Cite your sources after the question. Ensure you cover ALL parts of {goal} mentioned in the documents and that the question is fully self-contained in text form."
        
        # Add instruction to keep the question concise
        content += "\n\nIMPORTANT: Keep the question and options concise. The entire formatted question including all options must be under 1500 characters to fit within Discord's message limits."
        
        contents = []
        
        # Add PDF files to the contents if available
        if pdf_files:
            gemini_files = await self._get_gemini_files(pdf_files)
            for gemini_file in gemini_files:
                contents.append(f"Document name: {os.path.basename(self._get_file_path(gemini_file.name))}")
                contents.append(gemini_file)
        
        # Add the text content
        contents.append(content)
        
        response = self.client.models.generate_content(
            model=MODEL,
            contents=contents,
            config={
                "max_output_tokens": 1000,  # Limit response size
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
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "document_relevance": {
                                        "type": "string",
                                        "description": "Briefly explain why this document is relevant to the question"
                                    },
                                    "document_name": {
                                        "type": "string",
                                        "description": "The name of the document this question is based on"
                                    }
                                },
                                "required": ["document_relevance", "document_name"],
                                "propertyOrdering": ["document_relevance", "document_name"],
                            }
                        }
                    },
                    "required": ["question", "options", "correct_answers", "multiple_answers_allowed", "sources"],
                    "propertyOrdering": ["question", "options", "correct_answers", "multiple_answers_allowed", "sources"],
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
        if 'sources' in question_data and question_data['sources']:
            formatted_question += "\n\nSources:"
            for source in question_data['sources']:
                formatted_question += f"\n* {source['document_name']}: {source['document_relevance']}"

        print("--- formatted question ---")
        print(formatted_question)

        # After formatting the question, check its length and truncate if needed
        if len(formatted_question) > 1800:  # Leave some margin for additional text
            # Truncate and add a note
            formatted_question = formatted_question[:1750] + "...\n[Question truncated due to length]"
        
        # Return the formatted question, correct answers
        return formatted_question, question_data['correct_answers']

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
        
        message = "Welcome! Let's start by setting a learning goal."
        
        # Create a view with just the goal button
        view = InitialView(self)
        
        return message, view

    async def _save_attachment(self, attachment):
        """Save an attachment to disk and return the file path."""
        # Create directory for user if it doesn't exist
        user_dir = f"user_files/{self.user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        # Generate a filename based on the attachment name
        filename = attachment.filename
        filepath = f"{user_dir}/{filename}"
        
        # Download and save the file
        await attachment.save(filepath)
        return filepath
    
    def _get_file_mapping(self):
        """Get the file mapping for a user."""
        user_dir = f"user_files/{self.user_id}"
        os.makedirs(user_dir, exist_ok=True)
        
        mapping_file = f"{user_dir}/file_mapping.json"
        
        # Load existing mapping if it exists
        file_mapping = {}
        if os.path.exists(mapping_file):
            # Check if file is older than 1 hour
            file_mod_time = os.path.getmtime(mapping_file)
            current_time = time.time()
            one_hour_in_seconds = 3600
            
            if current_time - file_mod_time > one_hour_in_seconds:
                # File is older than 1 hour, return empty mapping
                print(f"Mapping file for user {self.user_id} is older than 1 hour, clearing cache")
                return {}, mapping_file
            
            # File is recent, load it
            with open(mapping_file, 'r') as f:
                file_mapping = json.load(f)
                
        return file_mapping, mapping_file
    
    def _save_file_mapping(self, mapping_file, file_mapping):
        """Save the file mapping to disk."""
        with open(mapping_file, 'w') as f:
            json.dump(file_mapping, f)
    
    def _get_file_path(self, file_id):
        """Get the filepath from a Gemini file ID."""
        file_mapping, _ = self._get_file_mapping()
        
        # Reverse lookup: find filepath by Gemini file ID
        for filepath, gemini_id in file_mapping.items():
            if gemini_id.removeprefix("files/") == file_id.removeprefix("files/"):
                return filepath
        
        return None
    
    async def _get_gemini_files(self, file_paths):
        """Upload files to Gemini API and return file objects."""
        gemini_files = []
        
        # Get file mapping
        file_mapping, mapping_file = self._get_file_mapping()
        
        # Process each file
        for filepath in file_paths:
            # Check if file is already uploaded
            if filepath in file_mapping:
                print(f"Using existing Gemini file for {filepath}")
                file_uri = file_mapping[filepath]
                gemini_files.append(self.client.files.get(name=file_uri))
            else:
                # Upload file to Gemini
                print(f"Uploading {filepath} to Gemini")
                gemini_file = self.client.files.upload(file=filepath)
                
                # Store the mapping
                file_mapping[filepath] = gemini_file.name
                gemini_files.append(gemini_file)
        
        # Save updated mapping
        self._save_file_mapping(mapping_file, file_mapping)

        return gemini_files
    
    def _parse_command(self, message_text: str):
        """Parse a command from a message"""
        if not message_text.startswith("!"):
            return Command.NONE, message_text
        
        parts = message_text[1:].split(" ", 1)
        command = parts[0].lower()
        argument = parts[1] if len(parts) > 1 else ""
        
        if command == "answer":
            # Normalize answer format
            argument = argument.strip().upper()
            if argument not in ["A", "B", "C", "D", "E", "NOT SURE"]:
                return Command.ANSWER, "INVALID"
            return Command.ANSWER, argument
        elif command == "ask":
            return Command.ASK, argument
        elif command == "goal":
            return Command.GOAL, argument
        elif command == "upload":
            return Command.UPLOAD, argument
        elif command == "quiz":
            return Command.QUIZ, argument
        elif command == "end":  # Add this new case
            return Command.END, argument
        else:
            return Command.NONE, message_text
        
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
            question, correct_answers = await self._generate_question(
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
        
        state["question"], state["correct_answers"] = await self._generate_question(message_content, question_history=None, pdf_files=state["pdf_files"])
        return state["question"]
        
    async def _handle_question_answer(self, user_answer, state):
        """Handle an answer from the user"""

        pdf_files = state['pdf_files']

        # If we're in a quiz, handle differently
        if state["state"] == UserState.IN_QUIZ:
            return await self._handle_quiz_answer(user_answer, state)
        
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
                state["question"], state["correct_answers"] = await self._generate_question(
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
            state["question"], state["correct_answers"] = await self._generate_question(
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
            state["question"], state["correct_answers"] = await self._generate_question(state["goal"], question_history=state["question_history"], pdf_files=pdf_files)
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
        state["question"], state["correct_answers"] = await self._generate_question(
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
                filepath = await self._save_attachment(attachment)
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
        """Grade a completed quiz and return results"""
        total_questions = len(quiz_state.questions)
        correct_count = 0
        
        # Create a list to store question results
        results = []
        
        # Zip questions and answers together
        for i, (user_answer, question_data) in enumerate(
            zip(quiz_state.user_answers, quiz_state.questions)
        ):
            # Unpack question data (handle both 2-value and 3-value tuples)
            if len(question_data) == 3:
                question, correct_answers, concept = question_data
            else:
                question, correct_answers = question_data
                concept = "General concept"  # Default concept
            
            # Convert to sets for comparison
            user_answer_set = set(user_answer if isinstance(user_answer, list) else [user_answer])
            correct_answer_set = set(correct_answers if isinstance(correct_answers, list) else [correct_answers])
            
            # Check if answer is correct
            is_correct = user_answer_set == correct_answer_set
            if is_correct:
                correct_count += 1
            
            # Format the user's answer for display
            if len(user_answer_set) == 1:
                user_answer_display = next(iter(user_answer_set))
            else:
                user_answer_display = ", ".join(sorted(user_answer_set))
            
            # Format the correct answer for display
            if len(correct_answer_set) == 1:
                correct_answer_display = next(iter(correct_answer_set))
            else:
                correct_answer_display = ", ".join(sorted(correct_answer_set))
            
            # Add to results
            results.append({
                "question": question,
                "user_answer": user_answer_display,
                "correct_answer": correct_answer_display,
                "is_correct": is_correct,
                "concept": concept
            })
        
        # Calculate score
        score_percent = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        # Generate summary message - keep it concise
        summary = (
            f"**Quiz Results**\n\n"
            f"Topic: {quiz_state.goal}\n"
            f"Score: {correct_count}/{total_questions} ({score_percent:.1f}%)\n\n"
        )
        
        # Add first question feedback - truncate if needed
        if results:
            first_result = results[0]
            # Get a shortened version of the question (first 200 chars)
            short_question = first_result['question'][:200]
            if len(first_result['question']) > 200:
                short_question += "..."
                
            summary += (
                f"**Question 1 of {total_questions}**\n\n"
                f"{short_question}\n\n"
                f"Your answer: {first_result['user_answer']}\n"
                f"Correct answer: {first_result['correct_answer']}\n"
                f"Result: {'✅ Correct' if first_result['is_correct'] else '❌ Incorrect'}\n"
                f"Concept: {first_result['concept']}\n"
            )
        
        # Create view for reviewing results
        view = QuizResultsView(self, results, 0)
        
        return summary, view

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
        question, correct_answers = await self._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        # Store the question
        quiz_state.questions.append((question, correct_answers))
        
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
        question, correct_answers = await self._generate_question(
            state["goal"],
            state["question_history"],
            state["pdf_files"]
        )
        
        quiz_state.questions.append((question, correct_answers))
        
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
        
        # Handle end session command - this takes priority over all other commands
        if command == Command.END:
            # Reset state but keep PDF files
            pdf_files = state.get("pdf_files", [])
            self.conversation_state[user_id] = {
                "state": UserState.INITIAL,
                "goal": None,
                "question": None,
                "correct_answers": [],
                "question_history": [],
                "pdf_files": pdf_files,
                "quiz_state": None
            }
            
            # Show initial view
            await message.channel.send(
                "Session ended. All progress has been reset.",
                view=InitialView(self.agent)
            )
            return None
        
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
                    state["state"] = UserState.ASKING_QUESTION
                    state["quiz_state"] = None
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
        
        # Send the feedback
        await interaction.response.defer()
        await self._send_long_message(interaction, feedback)
    
    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary, disabled=False)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        self.current_index = min(self.total_questions - 1, self.current_index + 1)
        self.update_button_states()
        
        # Get the feedback for the current question
        feedback = self._get_current_feedback()
        
        # Send the feedback
        await interaction.response.defer()
        await self._send_long_message(interaction, feedback)
    
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
            f"Correct answer: {result['correct_answer']}\n"
            f"Result: {'✅ Correct' if result['is_correct'] else '❌ Incorrect'}\n"
            f"Concept: {result['concept']}\n"
        )
        
        return feedback
    
    async def _send_long_message(self, interaction, content, view=None):
        """Handle sending messages that might exceed Discord's character limit"""
        # If no view is provided, use self
        if view is None:
            view = self
            
        # Discord has a 2000 character limit
        if len(content) <= 1900:  # Leave some margin
            await interaction.followup.send(content, view=view)
            return
            
        # Split the content into parts
        parts = []
        current_part = ""
        
        # Split by lines to avoid breaking in the middle of a line
        lines = content.split('\n')
        
        for line in lines:
            # If adding this line would exceed the limit, start a new part
            if len(current_part) + len(line) + 1 > 1900:
                parts.append(current_part)
                current_part = line
            else:
                if current_part:
                    current_part += '\n' + line
                else:
                    current_part = line
        
        # Add the last part if it's not empty
        if current_part:
            parts.append(current_part)
        
        # Send all parts except the last one without a view
        for i in range(len(parts) - 1):
            await interaction.followup.send(parts[i])
        
        # Send the last part with the view
        await interaction.followup.send(parts[-1], view=view)

class EndSessionView(View):
    def __init__(self, agent):
        super().__init__(timeout=None)
        self.agent = agent

    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger)
    async def end_session_button(self, interaction: discord.Interaction, button: Button):
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