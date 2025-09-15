#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kindle OCR Processing Script

Processes screenshots from Kindle app using Google Cloud Vision API,
converts to various formats, and uploads to Google Drive.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path
from markdown_it import MarkdownIt
from google.cloud import vision_v1
from google.oauth2 import service_account
from googleapiclient.discovery import build


def setup_google_credentials(service_account_path):
    """Set up Google Cloud credentials."""
    if not os.path.exists(service_account_path):
        raise FileNotFoundError(f"Service account file not found: {service_account_path}")
    
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = service_account_path
    return vision_v1.ImageAnnotatorClient()


def perform_ocr(vision_client, image_path):
    """Perform OCR on a single image."""
    try:
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision_v1.Image(content=content)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts and len(texts) > 0:
            return texts[0].description
        return ''
    except Exception as e:
        print(f'Error processing {image_path}: {e}')
        return ''


def process_images(folder_path, vision_client):
    """Process all screenshot images in the folder."""
    folder = Path(folder_path)
    image_files = list(folder.glob('screenshot_*.png'))
    image_files.sort()  # Ensure proper ordering
    
    if not image_files:
        raise ValueError(f"No screenshot images found in {folder_path}")
    
    print(f"Found {len(image_files)} images to process")
    
    ocr_text = ''
    ocr_output_file = folder / 'ocr_output.txt'
    
    with open(ocr_output_file, 'w', encoding='utf-8') as output:
        for i, image_path in enumerate(image_files, 1):
            print(f'Processing image {i}/{len(image_files)}: {image_path.name}')
            text = perform_ocr(vision_client, str(image_path))
            if text:
                ocr_text += text + '\n'
                output.write(text + '\n')
    
    return ocr_text, ocr_output_file


def generate_filename(ocr_text, max_length=10):
    """Generate a safe filename from OCR text."""
    if not ocr_text.strip():
        return 'OCR_Output'
    
    # Take first meaningful text
    lines = [line.strip() for line in ocr_text.split('\n') if line.strip()]
    if not lines:
        return 'OCR_Output'
    
    filename = lines[0][:max_length].strip()
    # Replace unsafe characters
    safe_chars = []
    for char in filename:
        if char.isalnum() or char in ' -_':
            safe_chars.append(char)
        else:
            safe_chars.append('_')
    
    result = ''.join(safe_chars).replace(' ', '_')
    return result if result else 'OCR_Output'


def create_markdown(ocr_text, output_path):
    """Convert OCR text to Markdown format."""
    md = MarkdownIt()
    md_text = md.render(ocr_text)
    
    with open(output_path, 'w', encoding='utf-8') as output:
        output.write(md_text)
    
    print(f"Markdown file created: {output_path}")
    return md_text


def upload_to_google_drive(md_content, filename, service_account_path, folder_id):
    """Upload content to Google Drive as a Google Doc."""
    try:
        SCOPES = ['https://www.googleapis.com/auth/drive.file']
        
        credentials = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        drive_service = build('drive', 'v3', credentials=credentials)
        
        # Create Google Doc
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'mimeType': 'application/vnd.google-apps.document'
        }
        document = drive_service.files().create(body=file_metadata, fields='id').execute()
        doc_id = document['id']
        
        # Add content to the document
        docs_service = build('docs', 'v1', credentials=credentials)
        requests = [{'insertText': {'location': {'index': 1}, 'text': md_content}}]
        docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
        
        print(f'Google Doc created: https://docs.google.com/document/d/{doc_id}')
        return doc_id
    except Exception as e:
        print(f'Error uploading to Google Drive: {e}')
        raise


def create_pdf(image_files, output_path, img2pdf_path='/opt/homebrew/bin/img2pdf'):
    """Create PDF from image files."""
    try:
        if not os.path.exists(img2pdf_path):
            img2pdf_path = 'img2pdf'  # Try system PATH
        
        cmd = [img2pdf_path] + [str(f) for f in image_files] + ['-o', str(output_path)]
        subprocess.run(cmd, check=True)
        print(f"PDF created: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f'Error creating PDF: {e}')
        raise
    except FileNotFoundError:
        print(f'img2pdf not found. Install with: brew install img2pdf')
        raise


def main():
    parser = argparse.ArgumentParser(description='Process Kindle screenshots with OCR')
    parser.add_argument('folder_path', help='Path to folder containing screenshots')
    parser.add_argument('--service-account', required=True, 
                       help='Path to Google Cloud service account JSON file')
    parser.add_argument('--drive-folder-id', required=True,
                       help='Google Drive folder ID for uploads')
    parser.add_argument('--skip-drive', action='store_true',
                       help='Skip Google Drive upload')
    parser.add_argument('--skip-pdf', action='store_true',
                       help='Skip PDF creation')
    
    args = parser.parse_args()
    
    try:
        # Setup
        folder_path = Path(args.folder_path)
        if not folder_path.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")
        
        print(f"Processing folder: {folder_path}")
        
        # Initialize Google Vision API
        vision_client = setup_google_credentials(args.service_account)
        
        # Process images with OCR
        ocr_text, ocr_file = process_images(folder_path, vision_client)
        
        if not ocr_text.strip():
            print("Warning: No text extracted from images")
            return
        
        # Generate filename
        filename = generate_filename(ocr_text)
        print(f"Generated filename: {filename}")
        
        # Create files
        md_file = folder_path / f"{filename}.md"
        txt_file = folder_path / f"{filename}.txt"
        pdf_file = folder_path / f"{filename}.pdf"
        
        # Save text file
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(ocr_text)
        print(f"Text file created: {txt_file}")
        
        # Create Markdown
        md_content = create_markdown(ocr_text, md_file)
        
        # Upload to Google Drive
        if not args.skip_drive:
            upload_to_google_drive(md_content, filename, args.service_account, args.drive_folder_id)
        
        # Create PDF
        if not args.skip_pdf:
            image_files = list(folder_path.glob('screenshot_*.png'))
            image_files.sort()
            if image_files:
                create_pdf(image_files, pdf_file)
        
        print(f"\nProcessing completed successfully!")
        print(f"Files created in: {folder_path}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()