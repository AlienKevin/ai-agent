import os
from google import genai
import discord
import json
import time


from model import QuizState, UserState, Command
from views.initial_view import InitialView
from views.mcq_view import MCQView
from views.quiz_mcq_view import QuizMCQView
from views.quiz_results_view import QuizResultsView
from views.study_mode_view import StudyModeView
from views.response_view import ResponseView

MODEL = "gemini-2.0-flash"

class StudyAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.conversation_state = {}  # Track state per user
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
        if pdf_files and 'sources' in question_data and question_data['sources']:
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

        print("_handle_question_answer")
        print('user_answer', user_answer)
        print('state', state)

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
        
        print('state["question"]', state["question"])
        print('user_answer_display', user_answer_display)
        print('correct_answers', correct_answers)
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

        print(state["question_history"])
        
        # Generate next question focusing on weak areas (but don't include it in the response)
        state["question"], state["correct_answers"] = await self._generate_question(
            state["goal"], 
            state["question_history"],
            state["pdf_files"]
        )
        
        # Return only the feedback
        if len(feedback) > 1900:  # Leave some buffer
            feedback = feedback[:1900] + "... (feedback truncated)"

        # Handle quiz end
        if state["state"] == UserState.IN_QUIZ:
            quiz_state = state["quiz_state"]
            if quiz_state.is_finished():
                # Quiz is over, grade it
                state["state"] = UserState.ASKING_QUESTION
                result = await self._grade_quiz(state)
                state["quiz_state"] = None
                return result
            else:
                return (
                    f"Answer recorded. Times up <t:{int(time.time()//1 + quiz_state.time_remaining())}:R>\n\n"
                    f"Next question:\n{state["question"]}"
                )
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

    async def _grade_quiz(self, state):
        """Grade a completed quiz and return results"""
        total_questions = len(state["question_history"])
        correct_count = 0
        
        # Calculate score
        score_percent = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        # Generate summary message - keep it concise
        summary = (
            f"**Quiz Results**\n\n"
            f"Topic: {state['goal']}\n"
            f"Score: {correct_count}/{total_questions} ({score_percent:.1f}%)\n\n"
        )
        
        # Add first question feedback - truncate if needed
        quiz_questions = state["question_history"][-state["quiz_state"].total_questions:]
        if state["question_history"]:
            first_result = quiz_questions[0]
            # Get a shortened version of the question (first 200 chars)
            short_question = first_result['question'][:200]
            if len(first_result['question']) > 200:
                short_question += "..."
                
            summary += (
                f"**Question 1 of {total_questions}**\n\n"
                f"{short_question}\n\n"
                f"Your answer: {first_result['user_answer']}\n"
                f"Correct answer: {', '.join(first_result['correct_answers'])}\n"
                f"Result: {'✅ Correct' if first_result['is_correct'] else '❌ Incorrect'}\n"
                f"Concept: {first_result['concept']}\n"
            )
        
        # Create view for reviewing results
        view = QuizResultsView(self, quiz_questions, 0)
        
        return summary, view

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

        quiz_state.total_questions += 1
        
        state["question"] = question
        state["correct_answers"] = correct_answers

        print('state', state)
        
        # Create MCQ view for quiz
        view = QuizMCQView(self, question, correct_answers, quiz_state)
        
        message = (
            f"Starting {duration_minutes}-minute quiz on {state['goal']}\n"
            f"Times up <t:{int(time.time()//1 + quiz_state.time_remaining())}:R>\n\n"
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
        
        # handles answers not part of a quiz
        if command == Command.ANSWER:
            if state["state"] == UserState.ASKING_QUESTION:
                return await self._handle_question_answer(argument, state)
            else:
                return "There's no active question to answer. Use `!goal [learning goal]` to start a new goal."
        
        # Handle regular messages
        if state["state"] == UserState.INITIAL:
            return await self._handle_initial_state(message.content, state)
        else:
            # Treat as a regular message - suggest using commands
            return "I didn't recognize that as a command."

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
