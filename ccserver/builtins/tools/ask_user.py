from .base import BuiltinTools, ToolParam, ToolResult

# Sentinel checked by Agent._handle_tools() to pause and collect user input.
ASK_USER_SIGNAL = "__ASK_USER__"


class BTAskUser(BuiltinTools):

    name = "AskUserQuestion"

    description = (
        "Ask the user one or more questions and pause execution until they respond. "
        "Use this when you need a decision, clarification, or preference from the user "
        "before you can proceed — for example, choosing between two implementation approaches "
        "or confirming a destructive action. "
        "Do NOT use for purely informational questions you can answer yourself. "
        "Each question requires a short header label, the question text, and 2-4 answer options. "
        "Execution resumes automatically once the user submits their answers."
    )

    params = {
        "questions": ToolParam(
            type="array",
            description="List of 1-4 questions to ask the user.",
            items={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask. Should end with a question mark.",
                    },
                    "header": {
                        "type": "string",
                        "description": "Short label (max 12 chars) shown as a chip, e.g. 'Auth method'.",
                    },
                    "options": {
                        "type": "array",
                        "description": "2-4 choices for the user. Each option needs a label and description.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string",
                                    "description": "Short display text (1-5 words).",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Explanation of what this option means.",
                                },
                            },
                            "required": ["label", "description"],
                        },
                    },
                    "multiSelect": {
                        "type": "boolean",
                        "description": "Set true if the user can select multiple options.",
                    },
                },
                "required": ["question", "header", "options", "multiSelect"],
            },
        ),
    }

    async def run(self, questions: list) -> ToolResult:
        # This method is never actually called.
        # Agent._handle_tools() intercepts "AskUserQuestion" calls,
        # emits an ask_user event, and waits for the user's answer
        # before returning it as the tool result.
        return ToolResult.ok(ASK_USER_SIGNAL)
