#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kindle OCR Processing Script

Processes screenshots from Kindle app using Google Gemini API for OCR.
"""

import sys
import argparse
import base64
from pathlib import Path
import google.generativeai as genai


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

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
    return model


def perform_ocr(gemini_model, image_path, save_individual=True):
    """Perform OCR on a single image using Gemini and optionally save individual file."""
    try:
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()

        # Encode image to base64
        image_b64 = base64.b64encode(image_data).decode()

        # Create image part for Gemini
        image_part = {
            'mime_type': 'image/png',
            'data': image_b64
        }

        # Prompt for OCR - focus on main content, ignore system UI, fix spacing
        prompt = """Extract only the main book/document content from this Kindle app screenshot.

        IGNORE:
        - System toolbar, menu bar, status bar
        - Time, date, battery indicators
        - Window controls, buttons
        - File menu items (File, Edit, View, etc.)
        - Any UI elements outside the main reading area

        EXTRACT ONLY:
        - The actual book text content
        - Chapter titles, headings
        - Main body text, paragraphs
        - Any text that is part of the actual book/document being read

        IMPORTANT FORMATTING:
        - Fix any unnecessary spacing between characters in words
        - Ensure proper word spacing and sentence flow
        - Join fragmented words that should be together
        - Maintain natural paragraph breaks
        - Keep the text readable and properly formatted

        Return only the clean, properly-spaced text content without OCR artifacts or formatting instructions."""

        response = gemini_model.generate_content([prompt, image_part])

        text_content = response.text.strip() if response.text else ''

        # Save individual OCR file if requested
        if save_individual and text_content:
            image_path_obj = Path(image_path)
            # Create individual OCR filename: screenshot_001.png -> ocr_001.txt or screenshot_1.png -> ocr_1.txt
            ocr_filename = image_path_obj.stem.replace('screenshot_', 'ocr_') + '.txt'
            ocr_path = image_path_obj.parent / ocr_filename

            with open(ocr_path, 'w', encoding='utf-8') as ocr_file:
                ocr_file.write(text_content)
            print(f'Individual OCR saved: {ocr_filename}')

        return text_content
    except Exception as e:
        print(f'Error processing {image_path}: {e}')
        # Still create an empty individual file to maintain sequence
        if save_individual:
            image_path_obj = Path(image_path)
            ocr_filename = image_path_obj.stem.replace('screenshot_', 'ocr_') + '.txt'
            ocr_path = image_path_obj.parent / ocr_filename
            with open(ocr_path, 'w', encoding='utf-8') as ocr_file:
                ocr_file.write('')
            print(f'Empty OCR file created due to error: {ocr_filename}')
        return ''


def merge_ocr_files(folder_path):
    """Merge individual OCR files into a single ocr_output.txt file."""
    folder = Path(folder_path)
    ocr_files = list(folder.glob('ocr_*.txt'))

    # Sort OCR files by number to maintain order
    def extract_number(filename):
        import re
        match = re.search(r'ocr_(\d+)\.txt', filename.name)
        return int(match.group(1)) if match else 0

    ocr_files.sort(key=extract_number)

    if not ocr_files:
        print("No individual OCR files found to merge")
        return '', None

    print(f"Merging {len(ocr_files)} OCR files...")

    merged_text = ''
    ocr_output_file = folder / 'ocr_output.txt'

    with open(ocr_output_file, 'w', encoding='utf-8') as output:
        for ocr_file in ocr_files:
            try:
                with open(ocr_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        merged_text += content + '\n'
                        output.write(content + '\n')
                print(f"Merged: {ocr_file.name}")
            except Exception as e:
                print(f"Error reading {ocr_file.name}: {e}")

    print(f"Merged OCR saved to: {ocr_output_file}")
    return merged_text, ocr_output_file


def process_images(folder_path, gemini_model):
    """Process all screenshot images in the folder, creating individual OCR files."""
    folder = Path(folder_path)
    image_files = list(folder.glob('screenshot_*.png'))

    # Sort numerically by extracting number from filename
    def extract_screenshot_number(filepath):
        import re
        match = re.search(r'screenshot_(\d+)\.png', filepath.name)
        return int(match.group(1)) if match else 0

    image_files.sort(key=extract_screenshot_number)  # Numeric ordering

    if not image_files:
        raise ValueError(f"No screenshot images found in {folder_path}")

    print(f"Found {len(image_files)} images to process")

    # Process each image individually, saving separate OCR files
    for i, image_path in enumerate(image_files, 1):
        print(f'Processing image {i}/{len(image_files)}: {image_path.name}')
        perform_ocr(gemini_model, str(image_path), save_individual=True)

    print("Individual OCR processing completed")

    # Now merge all individual OCR files
    return merge_ocr_files(folder_path)


def main():
    parser = argparse.ArgumentParser(description='Process Kindle screenshots with OCR')
    parser.add_argument('folder_path', help='Path to folder containing screenshots')
    parser.add_argument('--gemini-api-key', required=False,
                       help='Google Gemini API key (defaults to config.env)')

    args = parser.parse_args()

    try:
        # Load configuration
        config = load_config()

        # Setup
        folder_path = Path(args.folder_path)
        if not folder_path.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")

        print(f"Processing folder: {folder_path}")

        # Get API key from args or config
        api_key = args.gemini_api_key or config.get('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("Gemini API key is required. Set it in config.env or use --gemini-api-key")

        # Initialize Google Gemini API
        gemini_model = setup_gemini_client(api_key)

        # Process images with OCR
        ocr_text, ocr_output_file = process_images(folder_path, gemini_model)

        if not ocr_text.strip():
            print("Warning: No text extracted from images")
            return

        print(f"\nOCR processing completed successfully!")
        print(f"Merged OCR file: {ocr_output_file}")
        print(f"Individual OCR files saved in: {folder_path}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()