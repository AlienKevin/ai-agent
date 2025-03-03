import os
from google import genai
from google.genai import types
import discord
import json
import pathlib
import httpx
from enum import Enum, auto
import re

MODEL = "gemini-2.0-flash"

SYSTEM_PROMPT = """You are a StudyAgent that helps students learn. Follow these steps:
1. If the user hasn't specified a topic yet, ask them what topic they want to learn about
2. Generate multiple choice questions to test their understanding, focusing on areas they struggled with previously
3. When they answer, grade their response and provide helpful feedback
4. Continue with more questions on the same topic until they want to switch topics
Keep track of their performance to adapt questions to their needs."""

class UserState(Enum):
    INITIAL = auto()
    ASKING_QUESTION = auto()
    AWAITING_ANSWER = auto()

class Command(Enum):
    ANSWER = "answer"
    QUESTION = "question"
    TOPIC = "topic"
    UPLOAD = "upload"
    NONE = "none"

class StudyAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.conversation_state = {}  # Track state per user
        self.command_patterns = {
            Command.ANSWER: r'!answer\s+([A-Ea-e])',
            Command.QUESTION: r'!question\s+(.*)',
            Command.TOPIC: r'!topic\s+(.*)',
            Command.UPLOAD: r'!upload'
        }

    async def _generate_question(self, topic: str, weak_areas=None, question_history=None, pdf_files=None):
        """Generate a multiple choice question about the given topic."""
        content = f"Generate a multiple choice question about {topic}."
        if weak_areas:
            content += f"\nFocus on these weak areas if possible: {list(weak_areas)}"
        if question_history:
            content += f"\nPrevious questions: {question_history}"

        print("generating question")
        
        contents = []
        
        # Add PDF files to the contents if available
        if pdf_files:
            for pdf_path in pdf_files:
                contents.append(
                    types.Part.from_bytes(
                        data=pathlib.Path(pdf_path).read_bytes(),
                        mime_type='application/pdf',
                    )
                )
        
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
                            "description": "The multiple choice question text"
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
                        "correct_answer": {
                            "type": "string",
                            "enum": ["A", "B", "C", "D", "E"]
                        }
                    },
                    "required": ["question", "options", "correct_answer"]
                }
            }
        )

        print("response", response.text)

        question_data = json.loads(response.text)
        
        # Format the question text with options
        formatted_question = (
            f"{question_data['question']}\n\n"
            f"A) {question_data['options']['A']}\n"
            f"B) {question_data['options']['B']}\n"
            f"C) {question_data['options']['C']}\n"
            f"D) {question_data['options']['D']}"
        )
        
        # Add option E if it exists
        if 'E' in question_data['options']:
            formatted_question += f"\nE) {question_data['options']['E']}"
        
        return formatted_question, question_data['correct_answer']

    async def _evaluate_answer(self, question: str, user_answer: str, correct_answer: str, pdf_files=None):
        """Evaluate the user's answer and return feedback."""
        content = f"Question: {question}\nStudent answered: {user_answer}\nCorrect answer: {correct_answer}"

        try:
            print("evaluating answer")
            
            contents = []
            
            # Add PDF files to the contents if available
            if pdf_files:
                for pdf_path in pdf_files:
                    contents.append(
                        types.Part.from_bytes(
                            data=pathlib.Path(pdf_path).read_bytes(),
                            mime_type='application/pdf',
                        )
                    )
            
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
                                "description": "The specific concept being tested"
                            },
                            "feedback": {
                                "type": "string", 
                                "description": "Detailed explanation and feedback"
                            }
                        },
                        "required": ["correct", "concept", "feedback"]
                    }
                }
            )
            return json.loads(response.text)
        except:
            print(response)
            return None

    def _initialize_state(self, user_id: str):
        """Initialize conversation state for a new user."""
        # Check for existing PDF files in the user's directory
        pdf_files = []
        user_dir = f"user_files/{user_id}"
        if os.path.exists(user_dir):
            for filename in os.listdir(user_dir):
                if filename.lower().endswith('.pdf'):
                    pdf_files.append(f"{user_dir}/{filename}")
        
        self.conversation_state[user_id] = {
            "state": UserState.INITIAL,
            "topic": None,
            "question": None,
            "correct_answer": None,
            "question_history": [],  # Track previous questions and answers
            "weak_areas": set(),     # Track concepts user struggled with
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
            pdf_message = f"\n\nI found {len(pdf_files)} previously uploaded PDF document(s). You can use `!topic [subject]` to start learning from these materials."
            
        return (
            "How can I help you learn today? You can use the following commands:\n"
            "- `!topic [subject]` - Start learning about a specific topic\n"
            "- `!answer [A/B/C/D/E]` - Answer the current question\n"
            "- `!question [question]` - Ask any question about the topic\n"
            "- `!upload` - Upload PDF documents to study from (attach files with this command)"
            f"{pdf_message}"
        )

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
                    return command, match.group(1).upper()
                elif command in [Command.QUESTION, Command.TOPIC]:
                    return command, match.group(1)
                else:  # Command.UPLOAD
                    return command, None
        
        return Command.NONE, message_content
        
    async def _handle_question(self, question, topic, pdf_files=None):
        """Handle a question from the user about the topic"""
        content = f"Answer this question about {topic}: {question}"
        print("responding to question")
        
        contents = []
        contents = await self._add_pdf_contents(contents, pdf_files)
        contents.append(content)
        
        response = self.client.models.generate_content(
            model=MODEL,
            contents=contents,
            config={
                "max_output_tokens": 800,  # Limit response to fit in Discord's message limit
                "temperature": 0.7
            }
        )
        
        return response.text
        
    async def _handle_topic_switch(self, state, new_topic):
        """Handle a topic switch from the user"""
        state["state"] = UserState.INITIAL
        state["topic"] = new_topic if new_topic else None
        state["weak_areas"] = set()
        
        if state["topic"]:
            state["state"] = UserState.ASKING_QUESTION
            state["question"], state["correct_answer"] = await self._generate_question(state["topic"], pdf_files=state["pdf_files"])
            return state["question"]
        return "What new topic would you like to learn about? Use `!topic [subject]`"
        
    async def _handle_initial_state(self, message_content, state):
        """Handle the initial state when user is providing a topic"""
        state["topic"] = message_content
        state["state"] = UserState.ASKING_QUESTION
        
        state["question"], state["correct_answer"] = await self._generate_question(message_content, pdf_files=state["pdf_files"])
        return state["question"]
        
    async def _handle_question_answer(self, user_answer, state):
        """Handle when user is answering a question"""
        correct_answer = state["correct_answer"].strip().upper()
        
        eval_response = await self._evaluate_answer(state["question"], user_answer, correct_answer, pdf_files=state["pdf_files"])
        
        if eval_response is None:
            state["question"], state["correct_answer"] = await self._generate_question(state["topic"], pdf_files=state["pdf_files"])
            return f"Sorry, I couldn't grade your response.\n\nHere's a new question:\n{state['question']}"
        
        is_correct = eval_response["correct"]
        concept = eval_response["concept"]
        feedback = eval_response["feedback"]
        
        # Update history and weak areas
        state["question_history"].append({
            "question": state["question"],
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "concept": concept,
            "is_correct": is_correct
        })
        
        if not is_correct:
            state["weak_areas"].add(concept)
        
        # Generate next question focusing on weak areas
        state["question"], state["correct_answer"] = await self._generate_question(
            state["topic"], 
            state["weak_areas"],
            state["question_history"],
            state["pdf_files"]
        )
        
        return f"{feedback}\n\nNext question:\n{state['question']}\n\nUse `!answer [letter]` to answer, `!question [question]` to ask a question, or `!topic [subject]` to switch topics."

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
            return f"I've received {len(pdf_files)} PDF document(s). I'll use these to help with your learning. Use `!topic [subject]` to start learning about a specific topic from these materials."
        else:
            return "I can only process PDF files at the moment. Please send PDF documents with the `!upload` command."

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
        if command == Command.TOPIC:
            return await self._handle_topic_switch(state, argument)
            
        if command == Command.QUESTION:
            if state["topic"]:
                return await self._handle_question(argument, state["topic"], state["pdf_files"])
            else:
                return "Please set a topic first using `!topic [subject]`"
            
        if command == Command.ANSWER:
            if state["state"] == UserState.ASKING_QUESTION:
                return await self._handle_question_answer(argument, state)
            else:
                return "There's no active question to answer. Use `!topic [subject]` to start a new topic."
        
        # Handle regular messages (no command)
        if state["state"] == UserState.INITIAL:
            return await self._handle_initial_state(message.content, state)
        elif state["state"] == UserState.ASKING_QUESTION:
            # Treat as a regular message - suggest using commands
            return "I didn't recognize that as a command. Please use `!answer [A/B/C/D/E]` to answer the question, `!question [question]` to ask a question, or `!topic [subject]` to switch topics."
