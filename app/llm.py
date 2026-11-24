"""The one place that asks Claude for a structured JSON answer (job scoring and resume analysis)."""
import json

import anthropic

from .config import settings


class ClaudeError(RuntimeError):
    """Claude answered, but not with usable JSON (refused, cut off, malformed)."""


def make_client() -> anthropic.Anthropic | None:
    s = settings()
    if not s["anthropic_key"]:
        return None
    kwargs = {"api_key": s["anthropic_key"]}
    if s["anthropic_base_url"]:
        kwargs["base_url"] = s["anthropic_base_url"]
    return anthropic.Anthropic(**kwargs)


def ask_json(client, *, system: str, user: str, schema: dict, max_tokens: int = 4000,
             effort: str = "low") -> dict:
    """Send one request and return the parsed JSON object. Raises ClaudeError on a bad answer."""
    kwargs = dict(model=settings()["model"], max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
    try:
        response = client.messages.create(
            **kwargs,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.BadRequestError:
        # A proxy may not pass output_config through; ask for the JSON in the prompt instead.
        kwargs["messages"] = [{
            "role": "user",
            "content": user + "\n\nReply with only JSON that matches this schema:\n" + json.dumps(schema),
        }]
        response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("Claude's reply was cut off (max_tokens)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ClaudeError("Claude did not return JSON")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as err:
        raise ClaudeError(f"Claude returned invalid JSON ({err})") from err
