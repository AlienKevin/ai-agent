import os
from mistralai import Mistral
import discord
import json
import re

MISTRAL_MODEL = "mistral-large-latest"
SYSTEM_PROMPT = "You are a helpful assistant."

MCQ_GENERATION_PROMPT = """
Generate {num_questions} multiple-choice questions (MCQs) about the following topic:

Topic: {topic}

Each MCQ should have:
1. A clear question
2. Exactly 4 options (A, B, C, D)
3. The correct answer indicated

Format your response as a JSON array of objects with the following structure:
[
  {{
    "question": "The question text here",
    "options": {{
      "A": "First option",
      "B": "Second option",
      "C": "Third option",
      "D": "Fourth option"
    }},
    "answer": "A"
  }}
]

Return only the valid JSON with no additional text, explanation, or formatting.
"""

class MistralAgent:
    def __init__(self):
        MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
        self.client = Mistral(api_key=MISTRAL_API_KEY)
        self.active_quizzes = {}  # Store active quizzes by channel ID

    async def run(self, message: discord.Message):
        # The simplest form of an agent
        # Send the message's content to Mistral's API and return Mistral's response

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message.content},
        ]

        response = await self.client.chat.complete_async(
            model=MISTRAL_MODEL,
            messages=messages,
        )

        return response.choices[0].message.content
        
    async def generate_mcqs_from_topic(self, topic, num_questions=10):
        """Generate MCQs about a given topic using Mistral."""
        try:
            messages = [
                {"role": "system", "content": "You are an expert at creating multiple choice questions. Always format your response as valid JSON."},
                {"role": "user", "content": MCQ_GENERATION_PROMPT.format(
                    topic=topic,
                    num_questions=num_questions
                )},
            ]

            response = await self.client.chat.complete_async(
                model=MISTRAL_MODEL,
                messages=messages,
            )
            
            result = response.choices[0].message.content
            
            # Clean up the response to ensure valid JSON
            # Remove any markdown code blocks if present
            result = re.sub(r'```json', '', result)
            result = re.sub(r'```', '', result)
            
            # Find the JSON array in the response
            json_pattern = r'\[\s*{.*}\s*\]'
            json_match = re.search(json_pattern, result, re.DOTALL)
            
            if json_match:
                json_str = json_match.group(0)
                questions = json.loads(json_str)
            else:
                # If regex fails, try direct JSON parsing
                questions = json.loads(result)
                
            # Validate the structure of each question
            validated_questions = []
            for q in questions:
                if "question" in q and "options" in q and "answer" in q:
                    if all(key in q["options"] for key in ["A", "B", "C", "D"]):
                        validated_questions.append(q)
            
            # Limit to the requested number of questions
            return validated_questions[:num_questions]
        except Exception as e:
            # Generate fallback questions if JSON parsing fails
            return self._generate_fallback_questions(topic, num_questions)
    
    def _generate_fallback_questions(self, topic, num_questions=10):
        """Generate fallback questions if the API response has formatting issues."""
        # Create a simple set of questions as a fallback
        questions = []
        
        # Examples for different topics
        if "history" in topic.lower():
            questions = [
                {
                    "question": f"What is a key event related to {topic}?",
                    "options": {
                        "A": "Option 1", 
                        "B": "Option 2", 
                        "C": "Option 3", 
                        "D": "Option 4"
                    },
                    "answer": "A"
                }
            ]
        elif "science" in topic.lower():
            questions = [
                {
                    "question": f"What is a fundamental concept in {topic}?",
                    "options": {
                        "A": "Option 1", 
                        "B": "Option 2", 
                        "C": "Option 3", 
                        "D": "Option 4"
                    },
                    "answer": "B"
                }
            ]
        else:
            questions = [
                {
                    "question": f"Which of the following is true about {topic}?",
                    "options": {
                        "A": "Option 1", 
                        "B": "Option 2", 
                        "C": "Option 3", 
                        "D": "Option 4"
                    },
                    "answer": "C"
                }
            ]
        
        # Duplicate the question to get the requested number
        while len(questions) < num_questions:
            q = questions[0].copy()
            q["question"] = f"Question {len(questions) + 1} about {topic}?"
            questions.append(q)
            
        return questions
    
    def start_quiz(self, channel_id, questions):
        """Start a new quiz in a channel."""
        self.active_quizzes[channel_id] = {
            "questions": questions,
            "current_question": 0,
            "scores": {},  # User ID -> score
            "total_questions": len(questions)
        }
        return self.active_quizzes[channel_id]
    
    def get_active_quiz(self, channel_id):
        """Get the active quiz for a channel if it exists."""
        return self.active_quizzes.get(channel_id)
    
    def record_answer(self, channel_id, user_id, answer):
        """Record a user's answer and update score."""
        quiz = self.active_quizzes.get(channel_id)
        if not quiz:
            return None
            
        current_q_idx = quiz["current_question"] - 1  # -1 because we increment before sending the question
        if current_q_idx < 0 or current_q_idx >= len(quiz["questions"]):
            return False
            
        current_question = quiz["questions"][current_q_idx]
        is_correct = answer == current_question["answer"]
        
        # Initialize user score if not exists
        if user_id not in quiz["scores"]:
            quiz["scores"][user_id] = 0
            
        # Update score if correct
        if is_correct:
            quiz["scores"][user_id] += 1
            
        return {
            "is_correct": is_correct,
            "correct_answer": current_question["answer"],
            "score": quiz["scores"][user_id]
        }
    
    def next_question(self, channel_id):
        """Get the next question in the quiz."""
        quiz = self.active_quizzes.get(channel_id)
        if not quiz:
            return None
            
        # Increment question counter
        quiz["current_question"] += 1
        
        # Check if quiz is complete
        if quiz["current_question"] > len(quiz["questions"]):
            return "end"
            
        # Return the current question
        return quiz["questions"][quiz["current_question"] - 1]
    
    def end_quiz(self, channel_id):
        """End the quiz and return final scores."""
        quiz = self.active_quizzes.get(channel_id)
        if not quiz:
            return None
            
        final_results = {
            "total_questions": quiz["total_questions"],
            "scores": quiz["scores"]
        }
        
        # Remove the quiz from active quizzes
        del self.active_quizzes[channel_id]
        
        return final_results