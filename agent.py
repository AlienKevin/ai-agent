import os
from mistralai import Mistral
import discord
import json
MISTRAL_MODEL = "mistral-large-latest"
SYSTEM_PROMPT = """You are a StudyAgent that helps students learn. Follow these steps:
1. If the user hasn't specified a topic yet, ask them what topic they want to learn about
2. Generate multiple choice questions to test their understanding, focusing on areas they struggled with previously
3. When they answer, grade their response and provide helpful feedback
4. Continue with more questions on the same topic until they want to switch topics
Keep track of their performance to adapt questions to their needs."""

class StudyAgent:
    def __init__(self):
        MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
        self.client = Mistral(api_key=MISTRAL_API_KEY)
        self.conversation_state = {}  # Track state per user

    async def _generate_question(self, topic: str, weak_areas=None, question_history=None):
        """Generate a multiple choice question about the given topic."""
        content = f"Generate a multiple choice question about {topic}."
        if weak_areas:
            content += f"\nFocus on these weak areas if possible: {list(weak_areas)}"
        if question_history:
            content += f"\nPrevious questions: {question_history}"
        content += "\nInclude 4 options (A,B,C,D) and indicate the correct answer in a separate line starting with CORRECT:"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}
        ]

        response = await self.client.chat.complete_async(
            model=MISTRAL_MODEL,
            messages=messages,
        )

        question_response = response.choices[0].message.content
        parts = question_response.split("CORRECT:")
        return parts[0].strip(), parts[1].strip()

    async def _evaluate_answer(self, question: str, user_answer: str, correct_answer: str):
        """Evaluate the user's answer and return feedback."""
        messages = [
            {"role": "system", "content": "You are an educational assistant evaluating a student's answer. Return a JSON with format: {\"correct\": boolean, \"concept\": \"specific concept tested\", \"feedback\": \"detailed explanation\"}"},
            {"role": "user", "content": f"Question: {question}\nStudent answered: {user_answer}\nCorrect answer: {correct_answer}"}
        ]

        for attempt in range(3):
            try:
                response = await self.client.chat.complete_async(
                    model=MISTRAL_MODEL,
                    messages=messages,
                    response_format={"type": "json_object"}
                )

                eval_response = json.loads(response.choices[0].message.content)
                if (isinstance(eval_response["correct"], bool) and 
                    isinstance(eval_response["concept"], str) and 
                    isinstance(eval_response["feedback"], str)):
                    return eval_response
            except:
                if attempt == 2:
                    print(response)
                    return None
        return None

    def _initialize_state(self, user_id: str):
        """Initialize conversation state for a new user."""
        self.conversation_state[user_id] = {
            "state": "initial",
            "topic": None,
            "question": None,
            "correct_answer": None,
            "question_history": [],  # Track previous questions and answers
            "weak_areas": set()      # Track concepts user struggled with
        }
        return "How can I help you learn today?"

    async def run(self, message: discord.Message):
        user_id = str(message.author.id)
        
        if user_id not in self.conversation_state:
            return self._initialize_state(user_id)

        state = self.conversation_state[user_id]
        
        # Check if user wants to switch topics
        if "new topic" in message.content.lower() or "switch topic" in message.content.lower():
            state["state"] = "initial"
            state["topic"] = None
            state["weak_areas"] = set()
            return "What new topic would you like to learn about?"
            
        if state["state"] == "initial":
            # User is providing the topic
            state["topic"] = message.content
            state["state"] = "asking_question"
            
            state["question"], state["correct_answer"] = await self._generate_question(message.content)
            return state["question"]
            
        elif state["state"] == "asking_question":
            # User is answering the question
            user_answer = message.content.strip().upper()
            correct_answer = state["correct_answer"].strip().upper()
            
            eval_response = await self._evaluate_answer(state["question"], user_answer, correct_answer)
            
            if eval_response is None:
                state["question"], state["correct_answer"] = await self._generate_question(state["topic"])
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
                state["question_history"]
            )
            
            return f"{feedback}\n\nNext question:\n{state['question']}"
