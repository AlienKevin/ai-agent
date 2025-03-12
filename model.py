import time
from enum import Enum, auto

MODEL = "gemini-2.0-flash"

class QuizState:
    def __init__(self, duration_minutes: int, goal: str):
        self.start_time = time.time()
        self.duration_minutes = duration_minutes
        self.end_time = self.start_time + (duration_minutes * 60)
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
