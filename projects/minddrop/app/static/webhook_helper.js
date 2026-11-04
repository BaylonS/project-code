const form = document.querySelector("#testForm");
const notice = document.querySelector("#notice");
const webhookUrl = document.querySelector("#webhookUrl");

webhookUrl.textContent = `${window.location.origin}/api/intake`;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());
  const tags = data.tags ? data.tags.split(",").map((tag) => tag.trim()).filter(Boolean) : [];
  notice.textContent = "Sending...";
  try {
    const response = await fetch("/api/intake", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${data.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        user_email: data.user_email,
        body: data.body,
        source_type: "webhook-helper",
        visibility: data.visibility,
        tags,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || "Send failed");
    notice.textContent = `Saved: ${result.chunk.title}`;
    form.body.value = "";
  } catch (error) {
    notice.textContent = error.message;
  }
});
