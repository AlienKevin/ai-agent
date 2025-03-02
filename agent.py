import os
from google import genai
from google.genai import types
import discord
import json
import pathlib
import httpx

MODEL = "gemini-2.0-flash"

SYSTEM_PROMPT = """You are a StudyAgent that helps students learn. Follow these steps:
1. If the user hasn't specified a topic yet, ask them what topic they want to learn about
2. Generate multiple choice questions to test their understanding, focusing on areas they struggled with previously
3. When they answer, grade their response and provide helpful feedback
4. Continue with more questions on the same topic until they want to switch topics
Keep track of their performance to adapt questions to their needs."""

class StudyAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.conversation_state = {}  # Track state per user

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
                                }
                            },
                            "required": ["A", "B", "C", "D"]
                        },
                        "correct_answer": {
                            "type": "string",
                            "enum": ["A", "B", "C", "D"]
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
        self.conversation_state[user_id] = {
            "state": "initial",
            "topic": None,
            "question": None,
            "correct_answer": None,
            "question_history": [],  # Track previous questions and answers
            "weak_areas": set(),     # Track concepts user struggled with
            "pdf_files": []          # Store paths to saved PDF files
        }
        print("initialized state")
        return "How can I help you learn today? You can also send me PDF documents to study from."

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

    async def run(self, message: discord.Message):
        print("running on message", message.content)
        user_id = str(message.author.id)
        
        if user_id not in self.conversation_state:
            self._initialize_state(user_id)
        
        state = self.conversation_state[user_id]
        
        # Handle attachments (PDFs)
        if message.attachments:
            pdf_files = []
            for attachment in message.attachments:
                if attachment.filename.lower().endswith('.pdf'):
                    filepath = await self._save_attachment(attachment, user_id)
                    state["pdf_files"].append(filepath)
                    pdf_files.append(filepath)
            
            if pdf_files:
                return f"I've received {len(pdf_files)} PDF document(s). I'll use these to help with your learning. What topic would you like to explore from these materials?"
            else:
                return "I can only process PDF files at the moment. Please send PDF documents."
        
        # First check if this is a followup question or topic switch
        content = f"""User message: {message.content}
        Categorize the type of response the user gave.
        1. An answer can be A, B, C, D, or expression of uncertainty like not sure.
        2. A followup question is if the user is asking a question about the topic.
        3. A switch topic is if the user wants to learn about a new topic.
        """
        
        print("categorizing response")
        
        contents = []
        
        # Add PDF files to the contents if available
        if state["pdf_files"]:
            for pdf_path in state["pdf_files"]:
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
                'response_mime_type': 'application/json',
                'response_schema': {
                    "type": "object",
                    "properties": {
                        "response_type": {
                            "type": "string", 
                            "enum": ["answer", "followup_question", "switch_topic"],
                        },
                        "new_topic": {
                            "type": "string",
                            "description": "The new topic the user wants to learn about (if applicable)"
                        }
                    },
                    "required": ["response_type", "new_topic"]
                }
            }
        )
        
        try:
            msg_type = json.loads(response.text)
            
            # Validate the response format
            if not ("response_type" in msg_type and 
                   msg_type["response_type"] in ["answer", "followup_question", "switch_topic"] and
                   "new_topic" in msg_type):
                print("Invalid response format: ", response.text)
                msg_type = {"response_type": "answer", "new_topic": None}
        except json.JSONDecodeError:
            print("Failed to parse response: ", response.text) 
            msg_type = {"response_type": "answer", "new_topic": None}

        print("msg_type", msg_type)
        
        if msg_type["response_type"] == "switch_topic":
            state["state"] = "initial"
            state["topic"] = msg_type["new_topic"] if msg_type["new_topic"] else None
            state["weak_areas"] = set()
            if state["topic"]:
                state["state"] = "asking_question"
                state["question"], state["correct_answer"] = await self._generate_question(state["topic"], pdf_files=state["pdf_files"])
                return state["question"]
            return "What new topic would you like to learn about?"
            
        if msg_type["response_type"] == "followup_question":
            # User is asking a question about the topic
            content = f"Answer this question about {state['topic']}: {message.content}"
            print("responding to followup question")
            
            contents = []
            
            # Add PDF files to the contents if available
            if state["pdf_files"]:
                for pdf_path in state["pdf_files"]:
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
                contents=contents
            )
            return response.text
            
        if state["state"] == "initial":
            # User is providing the topic
            state["topic"] = message.content
            state["state"] = "asking_question"
            
            state["question"], state["correct_answer"] = await self._generate_question(message.content, pdf_files=state["pdf_files"])
            return state["question"]
            
        elif state["state"] == "asking_question":
            # User is answering the question
            user_answer = message.content.strip().upper()
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
            
            return f"{feedback}\n\nNext question:\n{state['question']}\n\nYou're welcome to ask me any followup questions or switch to another topic!"
