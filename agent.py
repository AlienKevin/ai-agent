import os
import litellm
from litellm import completion
import discord
import json

MODELS = {
    "mistral": "mistral/mistral-large-latest",
    "gemini": "gemini/gemini-2.0-flash"
}
CURRENT_MODEL = "gemini"

SYSTEM_PROMPT = """You are a StudyAgent that helps students learn. Follow these steps:
1. If the user hasn't specified a topic yet, ask them what topic they want to learn about
2. Generate multiple choice questions to test their understanding, focusing on areas they struggled with previously
3. When they answer, grade their response and provide helpful feedback
4. Continue with more questions on the same topic until they want to switch topics
Keep track of their performance to adapt questions to their needs."""

litellm.enable_json_schema_validation=True

class StudyAgent:
    def __init__(self):
        os.environ["MISTRAL_API_KEY"] = os.getenv("MISTRAL_API_KEY")
        os.environ["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY")
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

        response = completion(
            model=MODELS[CURRENT_MODEL],
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
                response = completion(
                    model=MODELS[CURRENT_MODEL],
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
        print("initialized state")
        return "How can I help you learn today?"

    async def run(self, message: discord.Message):
        print("running")
        user_id = str(message.author.id)
        
        if user_id not in self.conversation_state:
            return self._initialize_state(user_id)
        
        print("continuing")

        state = self.conversation_state[user_id]

        # First check if this is a followup question or topic switch
        messages = [
            {"role": "system", "content": "You are an assistant that categorizes user response to a question."},
            {"role": "user", "content": message.content}
        ]
        
        response = completion(
            model=MODELS[CURRENT_MODEL],
            messages=messages,
            response_format={
                "type": "json_object",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "response_type": {
                            "type": "string", 
                            "enum": ["answer", "followup_question", "switch_topic"]
                        },
                        "new_topic": {
                            "type": "string"
                        }
                    },
                    "required": ["response_type", "new_topic"]
                },
                "strict": True
            }
        )
        
        try:
            msg_type = json.loads(response.choices[0].message.content)
            
            # Validate the response format
            if not ("response_type" in msg_type and 
                   msg_type["response_type"] in ["answer", "followup_question", "switch_topic"] and
                   "new_topic" in msg_type):
                print("Invalid response format: ", response.choices[0].message.content)
                msg_type = {"response_type": "answer", "new_topic": None}
        except json.JSONDecodeError:
            print("Failed to parse response: ", response.choices[0].message.content) 
            msg_type = {"response_type": "answer", "new_topic": None}
        
        if msg_type["response_type"] == "switch_topic":
            state["state"] = "initial"
            state["topic"] = msg_type["new_topic"] if msg_type["new_topic"] else None
            state["weak_areas"] = set()
            if state["topic"]:
                state["state"] = "asking_question"
                state["question"], state["correct_answer"] = await self._generate_question(state["topic"])
                return state["question"]
            return "What new topic would you like to learn about?"
            
        if msg_type["response_type"] == "followup_question":
            # User is asking a question about the topic
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Answer this question about {state['topic']}: {message.content}"}
            ]
            response = completion(
                model=MODELS[CURRENT_MODEL],
                messages=messages
            )
            return response.choices[0].message.content
            
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
            
            return f"{feedback}\n\nNext question:\n{state['question']}\n\nYou're welcome to ask me any followup questions or switch to another topic!"
