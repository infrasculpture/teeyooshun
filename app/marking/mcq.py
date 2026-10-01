"""Multiple-choice marking by code alone: no API call, no cost.

Returns the same result shape as marker.mark_answer so attempts, feedback and
stats treat MCQs like any other question.
"""

from app.marking.rules import confidence


def mark_mcq(question: dict, choice: str) -> dict:
    correct = question["correct_option"]
    right = choice == correct
    label, reason = confidence(question["source"], [])
    return {
        "question_id": question["id"], "mode": "mcq",
        "marks_awarded": question["marks"] if right else 0, "max_marks": question["marks"],
        "valid_points": None, "counted_points": None, "level_awarded": None,
        "confidence": label, "confidence_reason": f"{reason}; marked by code",
        "flags": [], "error": None, "model": "code", "api_call_id": None, "usage": None,
        "model_output": None,
        "detail": {"chosen": choice, "correct_option": correct, "options": question["options"]},
    }
