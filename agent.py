import os
from mistralai import Mistral
import discord

MISTRAL_MODEL = "mistral-large-latest"
SYSTEM_PROMPT = "You are a study assistant that helps someone learn by giving them practice questions. " \
                "You ask the user what subject they want to study. " \
                "Then you ask them a question about that subject which has a single-word answer. " \
                "When they answer, tell them if they got the question right or wrong and why."


class MistralAgent:
    def __init__(self):
        MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

        self.client = Mistral(api_key=MISTRAL_API_KEY)

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
