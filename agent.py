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

MODEL = "gemini-2.0-flash-lite"

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

    async def _generate_question(self, state):
        """Generate a multiple choice question based on the conversation history in the state."""
        goal = state["goal"]
        question_history = state["question_history"]
        pdf_files = state["pdf_files"]
        
        # Base prompt
        content = f"Generate a challenging multiple choice question about {goal}."
        
        if question_history and len(question_history) > 0:
            content += f"\n\nThe student has already answered these questions:"
            for i, q in enumerate(question_history):
                content += f"\n{i+1}. Question: {q['question']}\n   User answered: {q['user_answer']}\n   Correct answer: {q['correct_answers']}"
        
        # Add context about document usage and comprehensive coverage
        if pdf_files and len(pdf_files) > 0:
            content += f"\n\nBase your question on content from the uploaded documents. These may be lecture slides, past exams, or textbook content. Extract specific concepts, examples, or problems from these materials to create an authentic question. Cite your sources after the question. Ensure you cover ALL parts of {goal} mentioned in the documents and that the question is fully self-contained in text form."
            
        print("--- generating question ---")
        print(content)

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
                                "required": ["document_relevance", "document_name"]
                            }
                        }
                    },
                    "required": ["question", "options", "correct_answers", "multiple_answers_allowed", "sources"]
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

        # Return the formatted question, correct answers, and concept tested
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
            
            # Don't need to reference sources when evaluating answer
            # # Add PDF files to the contents if available
            # if pdf_files:
            #     gemini_files = await self._get_gemini_files(pdf_files)
            #     for gemini_file in gemini_files:
            #         contents.append(gemini_file)
            
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
            
        return (
            "How can I help you learn today? You can use the following commands:\n"
            "- `!goal [learning goal]` - Set a learning goal for this session\n"
            "- `!answer [letter]` or `!answer [letters]` - Answer the current question (e.g., `!answer A` or `!answer A,B,C` for multiple answers)\n"
            "- `!answer not sure` - Skip the current question if you don't know the answer\n"
            "- `!ask [question]` - Ask any question related to the set goal\n"
            "- `!quiz [duration]` - Start a timed quiz on the current topic (e.g., `!quiz 10` for a 10 minute quiz)\n"
            "- `!upload` - Upload PDF documents to study from (attach files with this command)"
            f"{pdf_message}"
        )

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
        
    async def _add_pdf_contents(self, contents, pdf_files):
        """Helper method to add PDF files to contents list"""
        if pdf_files:
            for pdf_path in pdf_files:
                contents.append(
                    types.Part.from_bytes(
                        data=pathlib.Path(pdf_path).read_bytes(),
                        mime_type='application/pdf',
                    )
                )
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
        
    async def _handle_question(self, state, question):
        """Handle a question from the user about the goal"""
        content = f"Answer this question concisely within 2000 characters: {question}"
        print("responding to question")
        
        contents = []
        if state["pdf_files"]:
            gemini_files = await self._get_gemini_files(state["pdf_files"])
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
        
        return response.text
        
    async def _handle_goal_switch(self, state, new_goal):
        """Handle a goal switch from the user"""
        state["state"] = UserState.INITIAL
        state["goal"] = new_goal if new_goal else None
        
        if state["goal"]:
            state["state"] = UserState.ASKING_QUESTION
            state["question"], state["correct_answers"] = await self._generate_question(state)
            return state["question"]
        return "What learning goal would you like to set for this session? Use `!goal [learning goal]`"
        
    async def _handle_initial_state(self, message_content, state):
        """Handle the initial state when user is providing a goal"""
        state["goal"] = message_content
        state["state"] = UserState.ASKING_QUESTION
        
        state["question"], state["correct_answers"] = await self._generate_question(state)
        return state["question"]
        
    async def _handle_question_answer(self, user_answer, state):
        """Handle when user is answering a question"""
        # Handle "not sure" response
        if user_answer == "NOT SURE":
            # Generate feedback for "not sure" response
            correct_answers = state["correct_answers"]
            
            # Format correct answers for display
            if len(correct_answers) == 1:
                correct_answers_display = correct_answers[0]
            else:
                correct_answers_display = ", ".join(correct_answers)
            
            feedback = f"The correct answer{'s' if len(correct_answers) > 1 else ''} {'are' if len(correct_answers) > 1 else 'is'}: {correct_answers_display}. Let me explain:\n\n"
            
            # Add explanation based on the options
            question_parts = state["question"].split("\n\n")
            question_text = question_parts[0]
            options = question_parts[1].split("\n")
            
            # Find the correct option text for all correct answers
            correct_options_text = []
            for correct_answer in correct_answers:
                for option in options:
                    if option.startswith(f"{correct_answer})"):
                        correct_options_text.append(f"{correct_answer}) {option[3:].strip()}")
                        break
            
            # Generate an explanation
            content = f"Question: {question_text}\nCorrect answer{'s' if len(correct_answers) > 1 else ''}: {correct_answers_display}\n"
            for option_text in correct_options_text:
                content += f"- {option_text}\n"
            content += "\nExplain why these are correct concisely within 2000 characters."
            
            contents = []
            if state["pdf_files"]:
                gemini_files = await self._get_gemini_files(state["pdf_files"])
                for gemini_file in gemini_files:
                    contents.append(gemini_file)
            contents.append(content)
            
            response = self.client.models.generate_content(
                model=MODEL,
                contents=contents,
                config={
                    "max_output_tokens": 250,
                    "temperature": 0.7
                }
            )
            
            feedback += response.text
            
            # Update history
            state["question_history"].append({
                "question": state["question"],
                "user_answer": "NOT SURE",
                "correct_answers": correct_answers,
                "is_correct": False
            })
            
            # Generate next question
            state["question"], state["correct_answers"] = await self._generate_question(state)
            
            # Prepare response text
            response_text = f"{feedback}\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know."
            
            # Truncate if too long for Discord
            if len(response_text) > 1900:  # Leave some buffer
                # Truncate the feedback part while preserving the question and instructions
                max_feedback_length = 1900 - len(f"\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know.")
                truncated_feedback = feedback[:max_feedback_length] + "... (feedback truncated)"
                response_text = f"{truncated_feedback}\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know."
            
            return response_text
        
        # Handle invalid answer
        if user_answer == "INVALID":
            return f"Invalid answer format. Please use `!answer [letter]` (e.g., `!answer A`) or `!answer [letters]` (e.g., `!answer A,B,C`) or `!answer not sure`.\n\nThe current question is:\n{state['question']}"
        
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
        
        eval_response = await self._evaluate_answer(state["question"], user_answer_display, correct_answers, pdf_files=state["pdf_files"])
        
        if eval_response is None:
            state["question"], state["correct_answers"] = await self._generate_question(state)
            return f"Sorry, I couldn't grade your response.\n\nHere's a new question:\n{state['question']}"
        
        is_correct = eval_response["correct"]
        feedback = eval_response["feedback"]
        
        # Update history
        state["question_history"].append({
            "question": state["question"],
            "user_answer": user_answer_display,
            "correct_answers": correct_answers,
            "is_correct": is_correct
        })
        
        # Generate next question focusing on weak areas
        state["question"], state["correct_answers"] = await self._generate_question(state)
        # Add information about multiple answers if applicable
        multiple_answers_text = ""
        if len(state["correct_answers"]) > 1:
            multiple_answers_text = " (This question has multiple correct answers. Use `!answer A,B,C` format to select multiple options)"
        
        # Prepare response text
        if len(state["correct_answers"]) > 1:
            response_text = f"{feedback}\n\nNext question{multiple_answers_text}:\n{state['question']}\n\nUse `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know."
        else:
            response_text = f"{feedback}\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, or `!answer not sure` if you don't know."
        # Truncate if too long for Discord
        if len(response_text) > 1900:  # Leave some buffer
            # Truncate the feedback part while preserving the question and instructions
            max_feedback_length = 1900 - len(f"\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know.")
            truncated_feedback = feedback[:max_feedback_length] + "... (feedback truncated)"
            response_text = f"{truncated_feedback}\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` for a single answer, `!answer [letters]` for multiple answers (e.g., `!answer A,B,C`), or `!answer not sure` if you don't know."
        
        return response_text

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
            return f"I've received {len(pdf_files)} PDF document(s). I'll use these to help with your learning. Use `!goal [subject]` to set learning goal."
        else:
            return "I can only process PDF files at the moment. Please send PDF documents with the `!upload` command."

    async def _grade_quiz(self, quiz_state: QuizState) -> str:
        """Grade the quiz and return formatted results"""
        total_questions = len(quiz_state.questions)
        if total_questions == 0:
            return "No questions were answered during the quiz."

        correct_count = 0
        feedback = []
        
        for i, (question, user_answer) in enumerate(zip(quiz_state.questions, quiz_state.user_answers), 1):
            question_text, correct_answers = question
            
            # Evaluate the answer
            eval_result = await self._evaluate_answer(question_text, user_answer, correct_answers)
            is_correct = eval_result["correct"] if eval_result else False
            
            if is_correct:
                correct_count += 1
            
            # Format the question result
            feedback.append(f"\nQuestion {i}:")
            feedback.append(f"Your answer: {user_answer}")
            feedback.append(f"Correct answer(s): {', '.join(correct_answers)}")
            feedback.append(f"{'✅ Correct' if is_correct else '❌ Incorrect'}")
            if eval_result and eval_result["feedback"]:
                feedback.append(f"Explanation: {eval_result['feedback']}")
            feedback.append("")  # Empty line for spacing

        # Calculate score
        score_percentage = (correct_count / total_questions) * 100
        
        # Prepare summary
        summary = [
            f"Quiz Results ({quiz_state.duration_minutes} minutes)",
            f"Topic: {quiz_state.goal}",
            f"Score: {correct_count}/{total_questions} ({score_percentage:.1f}%)",
            "\nDetailed Feedback:",
        ]
        
        # Combine summary and feedback
        result = "\n".join(summary + feedback)
        
        # If result is too long for Discord, truncate the feedback section
        if len(result) > 1900:
            truncated_result = "\n".join(summary)
            remaining_length = 1900 - len(truncated_result) - len("\n... (some feedback omitted)")
            
            # Add as many feedback items as will fit
            current_length = len(truncated_result)
            for item in feedback:
                if current_length + len(item) + 1 < remaining_length:  # +1 for newline
                    truncated_result += "\n" + item
                    current_length += len(item) + 1
                else:
                    break
            
            result = truncated_result + "\n... (some feedback omitted)"
        
        return result

    async def _handle_quiz_command(self, duration_minutes: int, state: dict) -> str:
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
        question, correct_answers = await self._generate_question(state)
        
        quiz_state.questions.append((question, correct_answers))
        
        return (
            f"Starting {duration_minutes}-minute quiz on {state['goal']}\n"
            f"Time remaining: {quiz_state.format_time_remaining()}\n\n"
            f"{question}\n\n"
            f"Use `!answer [letter]` to submit your answer. Your answers will be graded when the time expires."
        )

    async def _handle_quiz_answer(self, user_answer: str, state: dict):
        """Handle an answer during a quiz"""
        quiz_state = state["quiz_state"]
        
        # Handle forced grading from timer expiration
        #if user_answer == "FORCE_GRADE":
        #    state["state"] = UserState.ASKING_QUESTION
        #    state["quiz_state"] = None
        #    return await self._grade_quiz(quiz_state)
        
        if quiz_state.is_finished():
            # Quiz is over, grade it
            state["state"] = UserState.ASKING_QUESTION
            state["quiz_state"] = None
            return await self._grade_quiz(quiz_state)
        
        # Record the answer
        quiz_state.user_answers.append(user_answer)
        
        # Generate next question
        question, correct_answers = await self._generate_question(state)
        
        # Store the question
        quiz_state.questions.append((question, correct_answers))
        
        return (
            f"Answer recorded. Time remaining: {quiz_state.format_time_remaining()}\n\n"
            f"Next question:\n{question}\n\n"
            f"Use `!answer [letter]` to submit your answer."
        )

    async def run(self, message: discord.Message):
        print("running on message", message.content)
        user_id = str(message.author.id)
        
        if user_id not in self.conversation_state:
            return self._initialize_state(user_id)
        
        state = self.conversation_state[user_id]
        
        # Parse the command from the message
        command, argument = self._parse_command(message.content)
        
        # Handle attachments with !upload command
        if command == Command.UPLOAD or (command == Command.NONE and message.attachments):
            return await self._handle_pdf_attachments(message)
        
        # Handle commands based on type
        if command == Command.GOAL:
            return await self._handle_goal_switch(state, argument)
            
        if command == Command.ASK:
            if state["goal"]:
                return await self._handle_question(state, argument)
            else:
                return "Please set a goal first using `!goal [learning goal]`"
        
        # Handle quiz command
        if command == Command.QUIZ:
            try:
                duration = int(argument)
                return await self._handle_quiz_command(duration, state)
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
            elif message.content == "!answer FORCE_GRADE":
                return await self._grade_quiz(quiz_state)
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
