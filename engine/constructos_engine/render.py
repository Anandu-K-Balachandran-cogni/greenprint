"""Photo-real renders of the 3D model with Gemini image generation.

The editor captures a view of its own 3D model (a room from eye height, or the current view), and
Gemini turns it into a photo-real image that keeps the geometry, openings and furniture exactly where
they are. The render is a visualisation; it never changes the model.
"""

from typing import Literal

from . import gemini

MAX_RENDERS_PER_PROJECT = 40

_PROMPT = {
    "room": ("Turn this 3D model view of a room into a photo-real interior photograph. Keep exactly the same camera, "
             "room shape, walls, doors, windows, furniture, their positions and colours. Replace the simple 3D models "
             "with realistic furniture, fabrics, wood, paint and flooring of the same colours; add natural daylight "
             "through the windows and soft shadows."),
    "view": ("Turn this 3D model view of a house into a photo-real architectural visualisation. Keep exactly the same "
             "camera, building shape, walls, openings, rooms and colours. Use realistic materials, natural daylight and "
             "soft shadows."),
}


def render(image: bytes, mime: str, kind: Literal["room", "view"], style: str | None = None) -> tuple[bytes, str, str]:
    """Return (image bytes, mime type, model)."""
    from google.genai import types
    model = gemini.select_image_model()
    prompt = _PROMPT[kind]
    if style:
        prompt += f" Style and finishes: {style.strip()[:600]}."
    prompt += " Do not add or remove rooms, walls, windows or furniture. No text, labels or watermarks."
    config = types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9"))
    response = gemini.generate_content(model=model, contents=[types.Content(role="user", parts=[
        types.Part.from_bytes(data=image, mime_type=mime), types.Part.from_text(text=prompt)])], config=config)
    for cand in response.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            if part.inline_data and (part.inline_data.mime_type or "").startswith("image/"):
                return part.inline_data.data, part.inline_data.mime_type, model
    raise RuntimeError("No picture came back. Please try again.")
