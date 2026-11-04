"""
TARS/JARVIS-style Pythonista starter app for a local OpenAI-compatible LLM.

LLM_URL and MODEL are set for the user's local LAN LLM.
For away-from-home use, replace the LAN IP with the LLM server's Tailscale IP
instead of exposing the port to the public internet.
"""

import json
import threading
import urllib.error
import urllib.request

try:
    import console
    import speech
    import ui
except ImportError:
    console = None
    speech = None
    ui = None


LLM_URL = "http://127.0.0.1:11434/v1/chat/completions"
MODEL = "llama3.1:8b"
API_KEY = ""
AIRNAVX_BRIDGE_URL = "http://127.0.0.1:15986/airnavx"

SYSTEM_PROMPT = """
You are a TARS-inspired personal assistant for the user.

Core personality:
- Practical, loyal, direct, and calm under pressure.
- Dry humor is allowed, but never at the cost of clarity.
- Humor level: 75%.
- Honesty level: 95%.
- Keep answers short unless the user asks for details.
- Be useful first, funny second.
- Do not pretend to know live system status unless a tool or API confirms it.
- If something involves safety, aircraft maintenance, electrical work, account access, purchases, or destructive system commands, slow down and verify before acting.

User context:
- Ask the user which projects and systems they want help with.

Preferred assistant behavior:
- Use concise, practical answers.
- Give step-by-step instructions when building, debugging, wiring, configuring, or coding.
- When giving code, keep it runnable and explain exactly what values the user needs to change.
- For local LLM setup, prefer private access through LAN or Tailscale. Do not recommend exposing the LLM port directly to the public internet.
- For Pythonista, assume iPhone constraints: simple UI, voice output, HTTP requests, local files, and lightweight dashboards.
- For home lab commands, confirm the target service and action before restarting, deleting, changing access, or modifying configuration.
- For aircraft-related topics, treat output as reference support only and remind the user to verify against official aircraft maintenance documentation when limits, procedures, or safety-critical values matter.
- Only use a manual workflow when the user explicitly loads a maintenance profile or provides a manual excerpt.
- If a maintenance tool or manual source is available in the current chat, ground the answer in that source. Otherwise say that the relevant manual excerpt is needed before answering.
- For work questions, keep the default profile generic. Use the source provided or an active maintenance tool if one is present, then give a concise answer with a reminder to verify against the current official procedure.

Useful project direction:
- Start with a Pythonista voice chat app that sends messages to a local OpenAI-compatible LLM API.
- Then add buttons and tools for Home Assistant, NAS status, Jellyfin status/restart, ADS-B aircraft, aircraft calculators, Star Citizen tools, KSP planner, ESP32 dashboards, and inventory tracking.
- The long-term goal is one iPhone control center for the user's home lab, aircraft tools, electronics projects, hobbies, and daily tasks.
"""


class LocalLLMClient:
    def __init__(self, url, model, api_key=""):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT.strip()}]

    def ask(self, user_text):
        self.messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": self.messages,
            "temperature": 0.7,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key

        request = urllib.request.Request(
            self.url,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", "replace")
            raise RuntimeError("LLM HTTP error %s: %s" % (exc.code, details))
        except urllib.error.URLError as exc:
            raise RuntimeError("Could not reach LLM server: %s" % exc.reason)

        result = json.loads(body)
        answer = result["choices"][0]["message"]["content"]
        self.messages.append({"role": "assistant", "content": answer})
        return answer


def say(text):
    if speech:
        speech.say(text)


class TarsApp(ui.View):
    def __init__(self):
        self.name = "TARS"
        self.background_color = "#111318"
        self.client = LocalLLMClient(LLM_URL, MODEL, API_KEY)
        self.is_waiting = False

        self.output = ui.TextView()
        self.output.editable = False
        self.output.background_color = "#181b22"
        self.output.text_color = "#f2f2f2"
        self.output.font = ("Menlo", 14)
        self.output.corner_radius = 6
        self.output.text = (
            "TARS online.\n"
            "Connected to the user's local LAN LLM config.\n\n"
        )
        self.add_subview(self.output)

        self.input = ui.TextField()
        self.input.placeholder = "Ask TARS..."
        self.input.background_color = "#f2f2f2"
        self.input.text_color = "#111111"
        self.input.corner_radius = 6
        self.input.clear_button_mode = "while_editing"
        self.input.action = self.send_message
        self.add_subview(self.input)

        self.send_button = ui.Button(title="Send")
        self.send_button.background_color = "#2d6cdf"
        self.send_button.tint_color = "white"
        self.send_button.corner_radius = 6
        self.send_button.action = self.send_message
        self.add_subview(self.send_button)

        self.clear_button = ui.Button(title="Clear")
        self.clear_button.background_color = "#3a3f4b"
        self.clear_button.tint_color = "white"
        self.clear_button.corner_radius = 6
        self.clear_button.action = self.clear_chat
        self.add_subview(self.clear_button)

    def layout(self):
        margin = 10
        button_w = 70
        button_gap = 8
        input_h = 42
        w = self.width
        h = self.height

        self.output.frame = (
            margin,
            margin,
            w - margin * 2,
            h - input_h - margin * 3,
        )
        self.clear_button.frame = (
            w - margin - button_w,
            h - margin - input_h,
            button_w,
            input_h,
        )
        self.send_button.frame = (
            self.clear_button.x - button_gap - button_w,
            h - margin - input_h,
            button_w,
            input_h,
        )
        self.input.frame = (
            margin,
            h - margin - input_h,
            self.send_button.x - button_gap - margin,
            input_h,
        )

    def append_line(self, speaker, text):
        self.output.text += "%s: %s\n\n" % (speaker, text)
        self.output.content_offset = (0, max(0, self.output.content_size[1]))

    def set_waiting(self, waiting):
        self.is_waiting = waiting
        self.send_button.enabled = not waiting
        self.send_button.title = "..." if waiting else "Send"

    def send_message(self, sender):
        if self.is_waiting:
            return

        user_text = self.input.text.strip()
        if not user_text:
            return

        self.input.text = ""
        self.append_line("the user", user_text)
        self.set_waiting(True)

        thread = threading.Thread(target=self.ask_in_background, args=(user_text,))
        thread.daemon = True
        thread.start()

    def ask_in_background(self, user_text):
        try:
            answer = self.client.ask(user_text)
        except Exception as exc:
            answer = "I hit an error. Very dramatic. Details: %s" % exc

        def update_ui():
            self.append_line("TARS", answer)
            self.set_waiting(False)
            say(answer)

        ui.delay(update_ui, 0)

    def clear_chat(self, sender):
        self.client.messages = [{"role": "system", "content": SYSTEM_PROMPT.strip()}]
        self.output.text = "TARS memory buffer cleared.\n\n"


def run_cli():
    client = LocalLLMClient(LLM_URL, MODEL, API_KEY)
    print("TARS online. Type exit to quit.")
    while True:
        user_text = input("the user > ").strip()
        if user_text.lower() in ("exit", "quit", "stop"):
            print("TARS: Goodbye, the user. Try not to break the universe.")
            break
        try:
            answer = client.ask(user_text)
        except Exception as exc:
            answer = "I hit an error. Very dramatic. Details: %s" % exc
        print("TARS: " + answer)


if __name__ == "__main__":
    if ui:
        if console:
            console.clear()
        app = TarsApp()
        app.present("fullscreen")
    else:
        run_cli()
