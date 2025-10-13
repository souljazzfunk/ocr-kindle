#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kindle Processing Pipeline

Convenience script that chains OCR processing and markdown conversion.
"""

import sys
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Process Kindle screenshots: OCR then Markdown conversion')
    parser.add_argument('folder_path', help='Path to folder containing screenshots')
    parser.add_argument('--gemini-api-key', required=False,
                       help='Google Gemini API key (defaults to config.env)')
    parser.add_argument('--drive-folder', required=False,
                       help='Local path to Google Drive sync folder')
    parser.add_argument('--skip-drive', action='store_true',
                       help='Skip copying files to Google Drive folder')
    parser.add_argument('--output-name', required=False,
                       help='Custom filename for output files (without extension)')

    args = parser.parse_args()

    try:
        # Step 1: Run OCR processing
        print("=== Step 1: OCR Processing ===")
        ocr_cmd = [sys.executable, 'img2txt.py', args.folder_path]
        if args.gemini_api_key:
            ocr_cmd.extend(['--gemini-api-key', args.gemini_api_key])

        result = subprocess.run(ocr_cmd, check=True)
        print("OCR processing completed successfully!")

        # Step 2: Run markdown conversion
        print("\n=== Step 2: Markdown Conversion ===")
        ocr_output_file = Path(args.folder_path) / 'ocr_output.txt'

        if not ocr_output_file.exists():
            raise FileNotFoundError(f"OCR output file not found: {ocr_output_file}")

        markdown_cmd = [sys.executable, 'txt2md.py', str(ocr_output_file)]
        if args.gemini_api_key:
            markdown_cmd.extend(['--gemini-api-key', args.gemini_api_key])
        if args.drive_folder:
            markdown_cmd.extend(['--drive-folder', args.drive_folder])
        if args.skip_drive:
            markdown_cmd.append('--skip-drive')
        if args.output_name:
            markdown_cmd.extend(['--output-name', args.output_name])

        result = subprocess.run(markdown_cmd, check=True)
        print("Markdown processing completed successfully!")

        print(f"\n=== Processing Complete ===")
        print(f"All files saved in: {args.folder_path}")

    except subprocess.CalledProcessError as e:
        print(f"Error running subprocess: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()