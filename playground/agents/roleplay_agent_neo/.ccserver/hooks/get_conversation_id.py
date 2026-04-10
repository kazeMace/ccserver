import os
import json
import sys


def main() -> None:
    parts = []
    conversation_id = os.environ.get("CONVERSATION_ID", "").strip()
    if conversation_id:
        parts.append(f"[CONVERSATION_ID] {conversation_id}")

    output = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(parts),
        },
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)

if __name__ == "__main__":
    main()
