import os
from google import genai
from google.genai import types
import discord
import json
import pathlib
from enum import Enum, auto
import re
import hashlib

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

class UserState(Enum):
    INITIAL = auto()
    ASKING_QUESTION = auto()
    AWAITING_ANSWER = auto()

class Command(Enum):
    ANSWER = "answer"
    QUESTION = "question"
    GOAL = "goal"
    UPLOAD = "upload"
    NONE = "none"

class StudyAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.conversation_state = {}  # Track state per user
        self.command_patterns = {
            Command.ANSWER: r'!answer\s+([A-Ea-e](?:[,\s]+[A-Ea-e])*|not sure)',
            Command.QUESTION: r'!question\s+(.*)',
            Command.GOAL: r'!goal\s+(.*)',
            Command.UPLOAD: r'!upload'
        }

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
        
        # Return the formatted question, correct answers, and concept tested
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
            
        return (
            "How can I help you learn today? You can use the following commands:\n"
            "- `!goal [learning goal]` - Set a learning goal for this session\n"
            "- `!answer [letter]` or `!answer [letters]` - Answer the current question (e.g., `!answer A` or `!answer A,B,C` for multiple answers)\n"
            "- `!answer not sure` - Skip the current question if you don't know the answer\n"
            "- `!question [question]` - Ask any question about the goal\n"
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
                    
                elif command in [Command.QUESTION, Command.GOAL]:
                    return command, match.group(1)
                else:  # Command.UPLOAD
                    return command, None
        return Command.NONE, message_content
        
    async def _handle_question(self, question, goal, pdf_files=None):
        """Handle a question from the user about the goal"""
        content = f"Answer this question: {question}"
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
        
        return response.text
        
    async def _handle_goal_switch(self, state, new_goal):
        """Handle a goal switch from the user"""
        state["state"] = UserState.INITIAL
        state["goal"] = new_goal if new_goal else None
        
        if state["goal"]:
            state["state"] = UserState.ASKING_QUESTION
            state["question"], state["correct_answers"], _ = await self._generate_question(state["goal"], question_history=None, pdf_files=state["pdf_files"])
            return state["question"]
        return "What learning goal would you like to set for this session? Use `!goal [learning goal]`"
        
    async def _handle_initial_state(self, message_content, state):
        """Handle the initial state when user is providing a goal"""
        state["goal"] = message_content
        state["state"] = UserState.ASKING_QUESTION
        
        state["question"], state["correct_answers"], _ = await self._generate_question(message_content, question_history=None, pdf_files=state["pdf_files"])
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
            content += "\nExplain why these are correct."
            
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
            
            # Identify the concept being tested
            concept_prompt = f"Based on this question, what specific concept is being tested?\nQuestion: {question_text}\n\nProvide a short, specific concept name (1-5 words)."
            
            concept_response = self.client.models.generate_content(
                model=MODEL,
                contents=[concept_prompt],
                config={
                    "max_output_tokens": 50,
                    "temperature": 0.2
                }
            )
            
            concept = concept_response.text.strip()
            
            # Update history
            state["question_history"].append({
                "question": state["question"],
                "user_answer": "NOT SURE",
                "correct_answers": correct_answers,
                "concept": concept,
                "is_correct": False
            })
            
            # Generate next question
            state["question"], state["correct_answers"], concept_tested = await self._generate_question(
                state["goal"], 
                state["question_history"],
                state["pdf_files"]
            )
            
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
            state["question"], state["correct_answers"], _ = await self._generate_question(state["goal"], question_history=state["question_history"], pdf_files=state["pdf_files"])
            return f"Sorry, I couldn't grade your response.\n\nHere's a new question:\n{state['question']}"
        
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
        
        # Generate next question focusing on weak areas
        state["question"], state["correct_answers"], concept_tested = await self._generate_question(
            state["goal"], 
            state["question_history"],
            state["pdf_files"]
        )
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
        if command == Command.GOAL:
            return await self._handle_goal_switch(state, argument)
            
        if command == Command.QUESTION:
            if state["goal"]:
                return await self._handle_question(argument, state["goal"], state["pdf_files"])
            else:
                return "Please set a goal first using `!goal [learning goal]`"
            
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
            return "I didn't recognize that as a command. Please use `!answer [A/B/C/D/E]` or `!answer not sure` to answer the question, `!question [question]` to ask a question, or `!topic [subject]` to switch topics."
