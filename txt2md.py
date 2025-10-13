#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kindle Markdown Processing Script

Converts OCR text to markdown format using Google Gemini API and uploads to Google Drive.
"""

import sys
import argparse
import shutil
from pathlib import Path
from google import genai

# Constants
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"

def load_config(config_path='config.env'):
    """Load configuration from config.env file."""
    config = {}
    config_file = Path(config_path)

    if not config_file.exists():
        return config

    try:
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Remove quotes if present
                    value = value.strip().strip('"').strip("'")
                    config[key.strip()] = value
    except Exception as e:
        print(f"Warning: Could not read config file {config_path}: {e}")

    return config


def setup_gemini_client(api_key):
    """Set up Google Gemini client."""
    if not api_key:
        raise ValueError("Gemini API key is required")

    client = genai.Client(api_key=api_key)
    return client


def generate_filename_from_text(gemini_client, text_content):
    """Generate a descriptive filename from text content using Gemini."""
    try:
        filename_prompt = f"""Based on this text content, generate a concise but descriptive filename for this document.

{text_content[:1000]}...

Rules:
- Extract the main title or topic in the original language
- Use only safe filename characters (letters, numbers, spaces, hyphens, underscores)
- If it's a book, use the book title
- If it's an article, use the main topic
- Return ONLY the filename without extension or explanations

Filename:"""

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=filename_prompt
        )

        if response.text:
            filename = response.text.strip()
            # Clean up the filename
            safe_chars = []
            for char in filename:
                if char.isalnum() or char in ' -_':
                    safe_chars.append(char)
                else:
                    safe_chars.append('_')

            result = ''.join(safe_chars).strip()
            return result if result else 'OCR_Output'
        else:
            return 'OCR_Output'

    except Exception as e:
        print(f"Error generating filename: {e}")
        return 'OCR_Output'




def main():
    parser = argparse.ArgumentParser(description='Convert OCR text to Markdown and upload to Google Drive')
    parser.add_argument('ocr_file', help='Path to OCR text file (e.g., ocr_output.txt)')
    parser.add_argument('--gemini-api-key', required=False,
                       help='Google Gemini API key (defaults to config.env)')
    parser.add_argument('--output-name', required=False,
                       help='Custom filename for output files (without extension)')

    args = parser.parse_args()

    try:
        # Load configuration
        config = load_config()

        # Setup
        ocr_file_path = Path(args.ocr_file)
        if not ocr_file_path.exists():
            raise FileNotFoundError(f"OCR file not found: {ocr_file_path}")

        print(f"Processing OCR file: {ocr_file_path}")

        # Get API key from args or config
        api_key = args.gemini_api_key or config.get('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("Gemini API key is required. Set it in config.env or use --gemini-api-key")

        # Initialize Google Gemini API
        gemini_client = setup_gemini_client(api_key)

        # Read OCR text
        with open(ocr_file_path, 'r', encoding='utf-8') as f:
            ocr_text = f.read().strip()

        if not ocr_text:
            print("Warning: OCR file is empty")
            return

        # Convert OCR text to markdown
        markdown_prompt = f"""Convert the following extracted text into well-formatted Markdown:

{ocr_text}

Rules:
- Use proper heading levels (# ## ###) for titles and chapter names
- Format paragraphs with proper line breaks
- Use **bold** for emphasis where appropriate
- Use > blockquotes for important quotes or highlighted text
- Maintain the original text structure and flow
- Do not add any content that wasn't in the original text
- Return only the Markdown-formatted content without explanations

Markdown:"""

        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=markdown_prompt
        )

        if not response.text:
            raise ValueError("Failed to generate markdown content")

        markdown_content = response.text.strip()

        # Generate filename from markdown content (unless custom name provided)
        if args.output_name:
            filename = args.output_name
        else:
            # Use first few lines of markdown for filename generation
            first_lines = '\n'.join(markdown_content.split('\n')[:10])
            filename = generate_filename_from_text(gemini_client, first_lines)

        print(f"Using filename: {filename}")

        # Save directly to Google Drive folder
        drive_folder = config.get('GOOGLE_DRIVE_FOLDER')
        if not drive_folder:
            raise ValueError("GOOGLE_DRIVE_FOLDER must be set in config.env")

        drive_folder_path = Path(drive_folder)
        if not drive_folder_path.exists():
            drive_folder_path.mkdir(parents=True, exist_ok=True)
            print(f"Created Google Drive folder: {drive_folder_path}")

        md_file = drive_folder_path / f"{filename}.md"
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        print(f"Markdown file created in Google Drive: {md_file}")
        print(f"\nProcessing completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()