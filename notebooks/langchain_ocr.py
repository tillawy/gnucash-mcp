import marimo

__generated_with = "0.25.0"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Image OCR with OpenAI Vision

    Extract text from images using `gpt-4o` vision.
    """)
    return


@app.cell
def _():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env")
    import os

    return (os,)


@app.cell
def _(os):
    import base64
    from pathlib import Path
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


    def ocr_image(image_path: str | Path) -> str:
        """Extract text from an image using OpenAI vision.

        Args:
            image_path: Path to the image file.

        Returns:
            Extracted text content from the image.
        """
        image_bytes = Path(image_path).read_bytes()
        b64 = base64.b64encode(image_bytes).decode()
        suffix = Path(image_path).suffix.lstrip(".").lower()
        media_type = "jpeg" if suffix in ("jpg", "jpeg") else suffix  # png, gif, webp

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{media_type};base64,{b64}"},
                        },
                        {"type": "text", "text": "Extract all text from this image verbatim."},
                    ],
                }
            ],
        )
        return response.choices[0].message.content


    # Usage
    text = ocr_image("/path/to/project/notebooks/sample-1.png")
    print(text)
    return


if __name__ == "__main__":
    app.run()
