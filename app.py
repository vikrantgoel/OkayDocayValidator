#!/usr/bin/env python3
"""
Complete PDF Validator Application
Features:
- Type-specific field extraction (text, checkbox, signature)
- Image-based signature detection
- Pre-parsed field access for Script3
- Common validation functions
- GitHub configuration management
- Thread-safe operations
"""

from flask import Flask, request, jsonify, render_template, send_file
import pdfplumber
import io
import base64
import tempfile
import uuid
import json
import os
import requests
from datetime import datetime
import fitz  # PyMuPDF for PDF highlighting
from PIL import Image, ImageDraw
import zipfile
import traceback
import sys
import gc
import psutil
import atexit
from threading import Lock
import numpy as np

from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB
app.config['SECRET_KEY'] = 'validator-secret-key-change-in-production'

# GitHub Configuration
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
GITHUB_REPO_OWNER = os.getenv('GITHUB_REPO_OWNER', 'your-username')
GITHUB_REPO_NAME = os.getenv('GITHUB_REPO_NAME', 'pdf-configs')

# Thread safety
validator_lock = Lock()


# Enhanced error handlers that always return JSON
@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': 'Internal server error occurred',
        'error_type': type(error.original_exception).__name__ if hasattr(error,
                                                                         'original_exception') else 'InternalServerError',
        'timestamp': datetime.now().isoformat()
    }), 500


@app.errorhandler(413)
def file_too_large(error):
    return jsonify({
        'success': False,
        'error': 'File too large. Maximum size is 100MB.',
        'error_type': 'RequestEntityTooLarge',
        'timestamp': datetime.now().isoformat()
    }), 413


@app.errorhandler(Exception)
def handle_all_errors(error):
    error_details = {
        'success': False,
        'error': f'{type(error).__name__}: {str(error)}',
        'error_type': type(error).__name__,
        'timestamp': datetime.now().isoformat()
    }

    print(f"❌ Exception handled: {error_details['error_type']}")
    traceback.print_exc()

    return jsonify(error_details), 500


class PDFValidator:
    def __init__(self):
        self.pdf_file = None
        self.pdf_path = None
        self.config_data = None
        self.python_scripts = {}
        self.actual_fields = {}
        self.expected_values = {}
        self.validation_results = {}
        self.temp_files_created = []
        self.max_temp_files = 10

    def get_memory_usage(self):
        """Get current memory usage in MB"""
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / 1024 / 1024
        except:
            return 0

    def cleanup_temp_files(self):
        """Clean up temporary files"""
        try:
            if self.pdf_path and os.path.exists(self.pdf_path):
                os.unlink(self.pdf_path)
                self.pdf_path = None

            while len(self.temp_files_created) > self.max_temp_files:
                old_file = self.temp_files_created.pop(0)
                try:
                    if os.path.exists(old_file):
                        os.unlink(old_file)
                except:
                    pass

            gc.collect()
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

    def load_pdf(self, pdf_path):
        """Load PDF file with proper resource management"""
        try:
            if self.pdf_file:
                try:
                    self.pdf_file.close()
                except:
                    pass

            self.cleanup_temp_files()
            self.pdf_file = pdfplumber.open(pdf_path)
            self.pdf_path = pdf_path
            self.temp_files_created.append(pdf_path)

            print(f"✅ PDF loaded: {len(self.pdf_file.pages)} pages")
            return True
        except Exception as e:
            print(f"❌ PDF load error: {e}")
            return False

    def load_config_from_github(self, file_type, config_filename=None):
        """Download configuration from GitHub"""
        if not GITHUB_TOKEN:
            return {'success': False, 'error': 'GitHub token not configured'}

        try:
            headers = {
                'Authorization': f'token {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'PDF-Validator-App/1.0'
            }

            if config_filename:
                # Download specific config file
                download_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}/{config_filename}'
                response = requests.get(download_url, headers=headers, timeout=30)

                if response.status_code != 200:
                    return {'success': False, 'error': f'Config file not found: {response.status_code}'}

                file_info = response.json()
                download_response = requests.get(file_info['download_url'], timeout=30)

                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                    temp_zip.write(download_response.content)
                    temp_zip_path = temp_zip.name
                    self.temp_files_created.append(temp_zip_path)

                return self.load_config_package(temp_zip_path)
            else:
                # Get latest config
                folder_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}'
                folder_response = requests.get(folder_url, headers=headers, timeout=30)

                if folder_response.status_code != 200:
                    return {'success': False, 'error': f'File type folder not found: {folder_response.status_code}'}

                folder_contents = folder_response.json()
                config_files = [item for item in folder_contents if item['name'].endswith('.zip')]

                if not config_files:
                    return {'success': False, 'error': f'No config packages found for: {file_type}'}

                latest_config = sorted(config_files, key=lambda x: x['name'], reverse=True)[0]
                download_response = requests.get(latest_config['download_url'], timeout=30)

                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                    temp_zip.write(download_response.content)
                    temp_zip_path = temp_zip.name
                    self.temp_files_created.append(temp_zip_path)

                return self.load_config_package(temp_zip_path)

        except Exception as e:
            return {'success': False, 'error': f'GitHub download error: {str(e)}'}

    def load_config_package(self, zip_path):
        """Load configuration package from ZIP file"""
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                file_list = zip_file.namelist()

                # Load JSON config
                config_files = [f for f in file_list if f.endswith('.json')]
                if config_files:
                    with zip_file.open(config_files[0]) as config_file:
                        config_content = config_file.read().decode('utf-8')
                        self.config_data = json.loads(config_content)
                        print(f"✅ Config loaded: {len(self.config_data.get('pages', {}))} pages")

                # Load Python scripts
                script_files = [f for f in file_list if f.endswith('.py')]
                for script_file in script_files:
                    with zip_file.open(script_file) as sf:
                        script_content = sf.read().decode('utf-8')
                        script_name = os.path.basename(script_file).replace('.py', '')
                        self.python_scripts[script_name] = script_content
                        print(f"✅ Script loaded: {script_name}")

            return {'success': True, 'config': self.config_data, 'scripts': self.python_scripts}
        except Exception as e:
            return {'success': False, 'error': f'Config load error: {str(e)}'}
        finally:
            try:
                if os.path.exists(zip_path):
                    os.unlink(zip_path)
            except:
                pass

    def _extract_field_by_type(self, page, field_config):
        """Extract field value based on its type - CORE LOGIC"""
        field_name = field_config['name']
        field_type = field_config['type']
        coordinates = field_config['coordinates']

        try:
            # Parse coordinates
            coords = [float(c.strip()) for c in coordinates.split(',')]
            if len(coords) != 4:
                return None

            x1, y1, x2, y2 = coords
            cropped_page = page.crop((x1, y1, x2, y2))

            if field_type == 'text':
                # TEXT: Simple text extraction
                actual_value = cropped_page.extract_text().strip()
                if actual_value:
                    actual_value = ' '.join(actual_value.split())  # Clean whitespace
                return actual_value

            elif field_type == 'checkbox':
                # CHECKBOX: Look for X marks - blank means unchecked, X means checked
                text_in_area = cropped_page.extract_text().strip()
                checkbox_indicators = ['X', 'x', '✓', '☑', '☒', '✔', '█', '■', '●']

                if any(indicator in text_in_area for indicator in checkbox_indicators):
                    return 'checked'
                else:
                    return 'unchecked'  # Blank = unchecked

            elif field_type == 'signature':
                # SIGNATURE: Image-based area analysis
                # Find page number for image analysis
                page_num = None
                for i, pdf_page in enumerate(self.pdf_file.pages):
                    if pdf_page == page:
                        page_num = i + 1
                        break

                if page_num:
                    return self._signature_image_analysis(page_num, coordinates)
                else:
                    return 'error'

            else:
                # Unknown type - default to text
                return cropped_page.extract_text().strip()

        except Exception as e:
            print(f"❌ Error extracting {field_name} ({field_type}): {e}")
            return None

    def _signature_image_analysis(self, page_num, coordinates, threshold=0.90):
        """Image-based signature detection - blank or filled area"""
        try:
            # Parse coordinates
            coords = [float(c.strip()) for c in coordinates.split(',')]
            x1, y1, x2, y2 = coords

            # Get PDF page and convert to image
            page_index = int(page_num) - 1
            page = self.pdf_file.pages[page_index]
            img = page.to_image(resolution=200, antialias=True)
            pil_img = img.original

            # Scale coordinates to image size
            scale_x = pil_img.width / page.width
            scale_y = pil_img.height / page.height

            pixel_x1 = int(x1 * scale_x)
            pixel_y1 = int(y1 * scale_y)
            pixel_x2 = int(x2 * scale_x)
            pixel_y2 = int(y2 * scale_y)

            # Crop signature area and analyze
            signature_area = pil_img.crop((pixel_x1, pixel_y1, pixel_x2, pixel_y2))
            gray_signature = signature_area.convert('L')
            pixel_array = np.array(gray_signature)

            # Count white pixels (empty area)
            white_threshold = 240
            white_pixels = np.sum(pixel_array > white_threshold)
            total_pixels = pixel_array.size
            white_percentage = white_pixels / total_pixels

            print(f"🖼️ Signature analysis: {white_percentage:.1%} white pixels")

            if white_percentage >= threshold:
                return 'blank'  # Mostly white = no signature
            else:
                return 'signed'  # Has content = signature present

        except Exception as e:
            print(f"❌ Signature analysis error: {e}")
            return 'error'

    def extract_actual_fields(self):
        """Extract all fields using type-specific logic"""
        if not self.pdf_file or not self.config_data:
            return {'success': False, 'error': 'PDF or config not loaded'}

        try:
            self.actual_fields = {}
            total_fields_found = 0

            for page_num, page_data in self.config_data.get('pages', {}).items():
                page_index = int(page_num) - 1

                if page_index >= len(self.pdf_file.pages):
                    print(f"⚠️ Page {page_num} not found in PDF")
                    continue

                page = self.pdf_file.pages[page_index]
                self.actual_fields[page_num] = {}

                # Process each field using type-specific extraction
                for field_config in page_data.get('fields', []):
                    field_name = field_config['name']
                    field_type = field_config['type']

                    print(f"🔍 Extracting {field_type} field: {field_name}")

                    # Use type-specific extraction
                    actual_value = self._extract_field_by_type(page, field_config)

                    self.actual_fields[page_num][field_name] = {
                        'type': field_type,
                        'value': actual_value,
                        'coordinates': field_config['coordinates']
                    }

                    total_fields_found += 1
                    print(f"✅ {field_name} ({field_type}): '{actual_value}'")

            print(f"🎯 Extracted {total_fields_found} fields using type-specific logic")
            return {'success': True, 'actual_fields': self.actual_fields, 'total_extracted': total_fields_found}

        except Exception as e:
            print(f"❌ Field extraction failed: {e}")
            return {'success': False, 'error': str(e)}

    def _parse_all_config_fields(self):
        """Pre-parse all config fields for Script3 access"""
        parsed_fields = {}

        for page_num, page_data in self.config_data.get('pages', {}).items():
            for field in page_data.get('fields', []):
                field_name = field['name']

                # Find field in actual extracted data
                if page_num in self.actual_fields and field_name in self.actual_fields[page_num]:
                    field_data = self.actual_fields[page_num][field_name]
                    parsed_fields[field_name] = {
                        'actual_value': field_data['value'],
                        'page': page_num,
                        'coordinates': field_data['coordinates'],
                        'field_type': field_data['type'],
                        'found': True
                    }
                else:
                    parsed_fields[field_name] = {
                        'actual_value': None,
                        'page': 'not_found',
                        'coordinates': 'unknown',
                        'field_type': 'unknown',
                        'found': False
                    }

        print(f"✅ Pre-parsed {len(parsed_fields)} config fields for Script3")
        return parsed_fields

    # Common utility functions for scripts
    def _find_field_in_pdf(self, field_name, actual_fields):
        """Common function to find a field in PDF data"""
        for page_num, page_fields in actual_fields.items():
            if field_name in page_fields:
                return {
                    'actual_value': page_fields[field_name]['value'],
                    'page': page_num,
                    'coordinates': page_fields[field_name]['coordinates'],
                    'field_type': page_fields[field_name]['type']
                }
        return None

    def _create_result_entry(self, field_name, expected_value, field_info, is_match):
        """Common function to create result entry"""
        return {
            'field_name': field_name,
            'field_type': field_info['field_type'] if field_info else 'text',
            'page': field_info['page'] if field_info else 'not_found',
            'coordinates': field_info['coordinates'] if field_info else 'unknown',
            'expected': expected_value,
            'actual': field_info['actual_value'] if field_info else 'NOT FOUND',
            'match': is_match
        }

    def _calculate_summary(self, matches, mismatches):
        """Common function to calculate summary"""
        total_fields = len(matches) + len(mismatches)
        matching_fields = len(matches)
        mismatched_fields = len(mismatches)
        accuracy_percentage = round((matching_fields / total_fields * 100), 2) if total_fields > 0 else 0

        return {
            'total_fields': total_fields,
            'matching_fields': matching_fields,
            'mismatched_fields': mismatched_fields,
            'accuracy_percentage': accuracy_percentage
        }

    def _simple_text_match(self, expected, actual):
        """Common function for simple text matching"""
        if not actual:
            return False
        return str(expected).lower().strip() == str(actual).lower().strip()

    def _currency_match(self, expected, actual, tolerance=0.01):
        """Common function for currency matching"""
        import re
        if not actual:
            return False

        expected_clean = re.sub(r'[$,]', '', str(expected))
        actual_clean = re.sub(r'[$,]', '', str(actual))

        try:
            expected_num = float(expected_clean)
            actual_num = float(actual_clean)
            return abs(expected_num - actual_num) <= tolerance
        except ValueError:
            return str(expected).lower().strip() == str(actual).lower().strip()

    def _checkbox_match(self, expected, actual):
        """Common function for checkbox matching"""
        if not actual:
            return False

        expected_checked = str(expected).lower() in ['checked', 'true', 'yes', '1']
        actual_checked = str(actual).lower() in ['checked', 'true', 'yes', '1']
        return expected_checked == actual_checked

    def _signature_match(self, expected, actual):
        """Common function for signature matching"""
        if not actual:
            return False

        expected_signed = str(expected).lower() in ['signed', 'present', 'yes']
        actual_signed = str(actual).lower() in ['signed', 'present', 'yes']
        return expected_signed == actual_signed

    def generate_expected_values(self, input_data):
        """Generate expected values using Script2"""
        if 'script2' not in self.python_scripts:
            self.expected_values = input_data
            return {'success': True, 'expected_values': self.expected_values}

        try:
            script2_code = self.python_scripts['script2']

            exec_globals = {
                'input_data': input_data,
                'config': self.config_data,
                'actual_fields': self.actual_fields,
                'json': json,
                'math': __import__('math'),
                'datetime': datetime,
                'expected_values': {},
                'print': print,
                'len': len, 'str': str, 'float': float, 'int': int, 'round': round
            }

            # Add input variables directly
            for key, value in input_data.items():
                try:
                    if isinstance(value, str) and value.strip():
                        clean_value = value.replace('$', '').replace(',', '').strip()
                        if clean_value.replace('.', '').replace('-', '').isdigit():
                            exec_globals[key] = clean_value # Shashank: changed this from exec_globals[key] = float(clean_value)
                        else:
                            exec_globals[key] = value
                    else:
                        exec_globals[key] = value
                except:
                    exec_globals[key] = value

            exec(script2_code, exec_globals)
            calculated_expected = exec_globals.get('expected_values', {})

            if calculated_expected:
                self.expected_values = calculated_expected
                print(f"✅ Script2 calculated {len(calculated_expected)} expected values")
            else:
                self.expected_values = input_data

            return {'success': True, 'expected_values': self.expected_values}

        except Exception as e:
            print(f"❌ Script2 error: {e}")
            self.expected_values = input_data
            return {'success': True, 'expected_values': self.expected_values, 'warning': f'Script2 error: {str(e)}'}

    def validate_fields(self):
        """Enhanced validation with pre-parsed fields for Script3"""
        if not self.expected_values or not self.actual_fields:
            return {'success': False, 'error': 'Expected or actual values not available'}

        try:
            if 'script3' in self.python_scripts:
                print("🧮 Using Script3 for validation")
                script3_code = self.python_scripts['script3']

                # Pre-parse all config fields
                parsed_fields = self._parse_all_config_fields()

                exec_globals = {
                    'expected_values': self.expected_values,
                    'actual_fields': self.actual_fields,
                    'config': self.config_data,
                    'validation_results': {},
                    'matches': [],
                    'mismatches': [],

                    # All fields directly available
                    'all_actual_fields': parsed_fields,

                    # Common utility functions
                    'find_field_in_pdf': self._find_field_in_pdf,
                    'create_result_entry': self._create_result_entry,
                    'calculate_summary': self._calculate_summary,
                    'simple_text_match': self._simple_text_match,
                    'currency_match': self._currency_match,
                    'checkbox_match': self._checkbox_match,
                    'signature_match': self._signature_match,

                    'print': print,
                    'len': len, 'str': str, 'float': float, 'int': int
                }

                # Add each field as individual variable (af_fieldname)
                for field_name, field_data in parsed_fields.items():
                    exec_globals[f'af_{field_name}'] = field_data['actual_value']
                    exec_globals[f'{field_name}_info'] = field_data

                exec(script3_code, exec_globals)

                script_results = exec_globals.get('validation_results', {})
                if script_results and 'summary' in script_results:
                    self.validation_results = script_results
                else:
                    # Build from components
                    matches = exec_globals.get('matches', [])
                    mismatches = exec_globals.get('mismatches', [])
                    self.validation_results = {
                        'matches': matches,
                        'mismatches': mismatches,
                        'summary': self._calculate_summary(matches, mismatches)
                    }
            else:
                print("📋 No Script3 found, using default validation")
                # self.validation_results = self._default_validation() #Shashank: commented out

            return {'success': True, 'validation_results': self.validation_results}

        except Exception as e:
            print(f"❌ Validation error: {e}")
            self.validation_results = self._default_validation()
            return {'success': True, 'validation_results': self.validation_results,
                    'warning': f'Script3 error: {str(e)}'}

    def _default_validation(self):
        """Default validation when no Script3"""
        matches = []
        mismatches = []

        for page_num, page_fields in self.actual_fields.items():
            for field_name, field_data in page_fields.items():
                expected_value = self.expected_values.get(field_name, '')
                actual_value = field_data['value']

                is_match = self._simple_text_match(expected_value, actual_value)

                result_entry = {
                    'field_name': field_name,
                    'field_type': field_data['type'],
                    'page': page_num,
                    'coordinates': field_data['coordinates'],
                    'expected': expected_value,
                    'actual': actual_value or 'NOT FOUND',
                    'match': is_match
                }

                if is_match:
                    matches.append(result_entry)
                else:
                    mismatches.append(result_entry)

        return {
            'matches': matches,
            'mismatches': mismatches,
            'summary': self._calculate_summary(matches, mismatches)
        }

    def generate_highlighted_pdf(self):
        """Generate PDF with highlighted mismatches"""
        if not self.validation_results or not self.pdf_path:
            return None

        try:
            pdf_bytes = io.BytesIO()
            pdf_doc = fitz.open(self.pdf_path)

            mismatches = self.validation_results.get('mismatches', [])
            for mismatch in mismatches:
                try:
                    page_num = int(mismatch['page']) - 1
                    coordinates = mismatch['coordinates']
                    coords = [float(c.strip()) for c in coordinates.split(',')]

                    if len(coords) == 4 and 0 <= page_num < len(pdf_doc):
                        x1, y1, x2, y2 = coords
                        page = pdf_doc[page_num]
                        rect = fitz.Rect(x1, y1, x2, y2)
                        highlight = page.add_highlight_annot(rect)
                        highlight.set_colors(stroke=[1, 0, 0])  # Red
                        highlight.update()
                except:
                    continue

            pdf_doc.save(pdf_bytes)
            pdf_doc.close()
            pdf_bytes.seek(0)
            return pdf_bytes

        except Exception as e:
            print(f"❌ PDF highlighting error: {e}")
            return None


# Global validator instance
validator = PDFValidator()


# Routes
@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('validator.html')


@app.route('/get_config_versions/<file_type>')
def get_config_versions(file_type):
    """Get specific configuration versions for a file type"""
    try:
        if not GITHUB_TOKEN:
            return jsonify({'success': False, 'error': 'GitHub token not configured'})

        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'PDF-Validator-App/1.0'
        }

        # Get contents of specific file type folder
        folder_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}'
        response = requests.get(folder_url, headers=headers, timeout=30)

        if response.status_code == 404:
            return jsonify({'success': False, 'error': f'File type "{file_type}" not found'})
        elif response.status_code == 403:
            return jsonify({'success': False, 'error': 'GitHub access forbidden. Check your token permissions.'})
        elif response.status_code == 429:
            return jsonify({'success': False, 'error': 'GitHub API rate limit exceeded. Please try again later.'})
        elif response.status_code != 200:
            return jsonify({'success': False, 'error': f'GitHub API error: {response.status_code}'})

        folder_contents = response.json()
        zip_files = [f for f in folder_contents if f['type'] == 'file' and f['name'].endswith('.zip')]

        if not zip_files:
            return jsonify({'success': False, 'error': f'No configurations found for "{file_type}"'})

        # Sort by name (newest first)
        zip_files.sort(key=lambda x: x['name'], reverse=True)

        configs = []
        for zip_file in zip_files:
            try:
                # Parse filename for better display
                filename = zip_file['name'].replace('.zip', '')
                parts = filename.split('_')

                if len(parts) >= 3 and len(parts[0]) >= 10:
                    date_part = parts[0]  # YYYY-MM-DD
                    time_part = parts[1]  # HH-MM-SS
                    config_type = '_'.join(parts[2:])
                    display_name = f"{date_part} {time_part.replace('-', ':')} - {config_type.replace('_', ' ').title()}"
                else:
                    display_name = filename.replace('_', ' ').title()

                configs.append({
                    'filename': zip_file['name'],
                    'display_name': display_name,
                    'download_url': zip_file['download_url'],
                    'size': round(zip_file['size'] / 1024, 1),  # Size in KB
                    'last_modified': zip_file.get('last_modified', 'Unknown')
                })
            except Exception as e:
                print(f"⚠️ Error processing config file {zip_file.get('name', 'unknown')}: {e}")
                continue

        return jsonify({
            'success': True,
            'file_type': file_type,
            'configs': configs,
            'total_configs': len(configs)
        })

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'GitHub request timed out. Please try again.'})
    except Exception as e:
        print(f"❌ Error fetching config versions: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Error fetching config versions: {str(e)}'})


@app.route('/get_available_file_types')
def get_available_file_types():
    """Get list of available file types from GitHub"""
    try:
        if not GITHUB_TOKEN:
            return jsonify({'success': False, 'error': 'GitHub token not configured', 'file_types': []})

        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'PDF-Validator-App/1.0'
        }

        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs'
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            return jsonify({'success': False, 'error': f'GitHub API error: {response.status_code}', 'file_types': []})

        contents = response.json()
        file_types = []

        for item in contents:
            if item['type'] == 'dir':
                folder_name = item['name']
                folder_url = f'{url}/{folder_name}'
                folder_response = requests.get(folder_url, headers=headers, timeout=30)

                if folder_response.status_code == 200:
                    folder_contents = folder_response.json()
                    zip_files = [f for f in folder_contents if f['name'].endswith('.zip')]

                    if zip_files:
                        configs = []
                        for zip_file in zip_files:
                            configs.append({
                                'filename': zip_file['name'],
                                'display_name': zip_file['name'].replace('.zip', ''),
                                'size': round(zip_file['size'] / 1024, 1)
                            })

                        file_types.append({
                            'name': folder_name,
                            'display_name': folder_name.replace('_', ' ').title(),
                            'config_count': len(configs),
                            'configs': configs
                        })

        return jsonify({'success': True, 'file_types': file_types, 'total_types': len(file_types)})

    except Exception as e:
        return jsonify({'success': False, 'error': f'Error fetching file types: {str(e)}', 'file_types': []})


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    """Upload PDF and process with configuration"""
    with validator_lock:
        try:
            if 'pdf_file' not in request.files:
                return jsonify({'success': False, 'error': 'No PDF file uploaded'})

            pdf_file = request.files['pdf_file']
            file_type = request.form.get('file_type', '').strip()
            config_filename = request.form.get('config_filename', '').strip()

            if not file_type:
                return jsonify({'success': False, 'error': 'Please select a file type'})

            if not pdf_file.filename or not pdf_file.filename.lower().endswith('.pdf'):
                return jsonify({'success': False, 'error': 'File must be a PDF'})

            # Save PDF
            validator.cleanup_temp_files()
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
                pdf_file.save(temp_pdf.name)
                pdf_path = temp_pdf.name

            # Load PDF
            if not validator.load_pdf(pdf_path):
                return jsonify({'success': False, 'error': 'Failed to load PDF file'})

            # Download config from GitHub
            if config_filename:
                config_result = validator.load_config_from_github(file_type, config_filename)
            else:
                config_result = validator.load_config_from_github(file_type)

            if not config_result['success']:
                return jsonify(config_result)

            # Extract actual fields using type-specific logic
            extraction_result = validator.extract_actual_fields()
            if not extraction_result['success']:
                return jsonify(extraction_result)

            total_fields = extraction_result.get('total_extracted', 0)

            return jsonify({
                'success': True,
                'message': f'PDF and configuration loaded successfully',
                'config': validator.config_data,
                'actual_fields': validator.actual_fields,
                'scripts_available': list(validator.python_scripts.keys()),
                'config_info': {
                    'file_type': file_type,
                    'config_filename': config_filename or 'latest',
                    'total_fields': total_fields
                },
                'debug_info': {
                    'pdf_pages': len(validator.pdf_file.pages),
                    'config_pages': len(validator.config_data.get('pages', {})),
                    'scripts_loaded': len(validator.python_scripts),
                    'fields_extracted': total_fields,
                    'memory_usage_mb': validator.get_memory_usage()
                }
            })

        except Exception as e:
            print(f"❌ Upload error: {e}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'})


@app.route('/generate_input_form', methods=['GET'])
def generate_input_form():
    """Generate input form using Script1"""
    try:
        if not validator.config_data:
            return jsonify({'success': False, 'error': 'No configuration loaded'})

        form_variables = []
        script1_warning = None

        if 'script1' in validator.python_scripts:
            try:
                script1_code = validator.python_scripts['script1']
                exec_globals = {
                    'config': validator.config_data,
                    'actual_fields': validator.actual_fields,
                    'form_variables': [],
                    'print': print
                }

                exec(script1_code, exec_globals)
                form_variables = exec_globals.get('form_variables', [])
                print(f"✅ Script1 provided {len(form_variables)} form variables")

            except Exception as e:
                print(f"❌ Script1 error: {e}")
                script1_warning = f'Script1 error: {str(e)}'

        form_html = _generate_form_from_variables(form_variables)
        response_data = {'success': True, 'form_html': form_html}
        if script1_warning:
            response_data['warning'] = script1_warning

        return jsonify(response_data)

    except Exception as e:
        print(f"❌ Form generation error: {e}")
        form_html = _generate_config_based_form()
        return jsonify({'success': True, 'form_html': form_html, 'warning': f'Form generation error: {str(e)}'})


def _generate_form_from_variables(form_variables):
    """Generate HTML form from Script1 variables"""
    form_html = '<form id="validation-form" class="validation-form">'
    form_html += '<h3>📝 Enter Expected Values</h3>'
    form_html += '<p class="form-description">Fill in the values you expect to find in the PDF</p>'

    if form_variables:
        form_html += '<div class="form-section">'
        form_html += '<h4>📊 Input Variables (from Script1)</h4>'

        for variable in form_variables:
            display_name = variable.replace('_', ' ').title()
            form_html += f'''
            <div class="form-group">
                <label for="{variable}" class="variable-label">📈 {display_name}</label>
                <input type="text" id="{variable}" name="{variable}" class="form-control variable-input" 
                       placeholder="Enter {display_name.lower()}" required>
                <div class="variable-hint">This will be used in Script2 calculations</div>
            </div>
            '''
        form_html += '</div>'
    else:
        form_html += _generate_config_fields_section()

    form_html += '<button type="submit" class="btn btn-primary">🔍 Calculate & Validate</button>'
    form_html += '</form>'
    return form_html


def _generate_config_fields_section():
    """Generate form from config fields when no Script1"""
    section_html = '<div class="form-section">'
    section_html += '<h4>📄 Expected Field Values</h4>'

    for page_num, page_data in validator.config_data.get('pages', {}).items():
        section_html += f'<div class="page-subsection"><h5>Page {page_num}</h5>'

        for field in page_data.get('fields', []):
            field_name = field['name']
            field_type = field['type']

            section_html += f'''
            <div class="form-group">
                <label for="{field_name}">{field_name.replace("_", " ").title()}</label>
                <span class="field-type-badge {field_type}">({field_type})</span>
            '''

            if field_type == 'text':
                section_html += f'<input type="text" id="{field_name}" name="{field_name}" class="form-control">'
            elif field_type == 'checkbox':
                section_html += f'''
                <select id="{field_name}" name="{field_name}" class="form-control">
                    <option value="unchecked">Unchecked</option>
                    <option value="checked">Checked</option>
                </select>
                '''
            elif field_type == 'signature':
                section_html += f'''
                <select id="{field_name}" name="{field_name}" class="form-control">
                    <option value="blank">Blank</option>
                    <option value="signed">Signed</option>
                </select>
                '''
            else:
                section_html += f'<input type="text" id="{field_name}" name="{field_name}" class="form-control">'

            section_html += '</div>'
        section_html += '</div>'
    section_html += '</div>'
    return section_html


def _generate_config_based_form():
    """Fallback form generation"""
    if not validator.config_data:
        return '<p>No configuration available</p>'

    form_html = '<form id="validation-form" class="validation-form">'
    form_html += _generate_config_fields_section()
    form_html += '<button type="submit" class="btn btn-primary">Validate PDF</button>'
    form_html += '</form>'
    return form_html


@app.route('/validate_pdf', methods=['POST'])
def validate_pdf():
    """Validate PDF using Script2 and Script3"""
    with validator_lock:
        try:
            input_data = request.get_json()
            if not input_data:
                return jsonify({'success': False, 'error': 'No input data provided'})

            if not validator.config_data or not validator.actual_fields:
                return jsonify({'success': False, 'error': 'PDF not loaded or fields not extracted'})

            # Generate expected values using Script2
            expected_result = validator.generate_expected_values(input_data)
            if not expected_result['success']:
                return jsonify(expected_result)

            # Validate fields using Script3
            validation_result = validator.validate_fields()
            if not validation_result['success']:
                return jsonify(validation_result)

            return jsonify({
                'success': True,
                'validation_results': validator.validation_results,
                'expected_values': validator.expected_values,
                'actual_fields': validator.actual_fields,
                'debug_info': {
                    'script2_warning': expected_result.get('warning'),
                    'script3_warning': validation_result.get('warning'),
                    'total_expected': len(validator.expected_values),
                    'total_actual_fields': sum(len(page.values()) for page in validator.actual_fields.values()),
                    'memory_usage_mb': validator.get_memory_usage()
                }
            })

        except Exception as e:
            print(f"❌ Validation error: {e}")
            traceback.print_exc()
            return jsonify({'success': False, 'error': f'Validation failed: {str(e)}'})


@app.route('/get_page/<int:page_num>')
def get_page(page_num):
    """Render PDF page for visual field location"""
    try:
        if not validator.pdf_file:
            return jsonify({'success': False, 'error': 'No PDF loaded'})

        scale = float(request.args.get('scale', 1.0))
        if scale <= 0 or scale > 5:
            scale = 1.0

        if page_num < 1 or page_num > len(validator.pdf_file.pages):
            return jsonify(
                {'success': False, 'error': f'Invalid page number. PDF has {len(validator.pdf_file.pages)} pages.'})

        page = validator.pdf_file.pages[page_num - 1]
        resolution = max(100, min(300, int(150 * scale)))

        img = page.to_image(resolution=resolution, antialias=True)
        pil_img = img.original

        # Resize if too large
        max_dimension = 2048
        if pil_img.width > max_dimension or pil_img.height > max_dimension:
            ratio = min(max_dimension / pil_img.width, max_dimension / pil_img.height)
            new_size = (int(pil_img.width * ratio), int(pil_img.height * ratio))
            pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)

        # Convert to base64
        img_buffer = io.BytesIO()
        pil_img.save(img_buffer, format='PNG', optimize=True)
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

        return jsonify({
            'success': True,
            'image': f'data:image/png;base64,{img_base64}',
            'display_width': pil_img.width,
            'display_height': pil_img.height,
            'pdf_width': page.width,
            'pdf_height': page.height,
            'page_number': page_num,
            'resolution': resolution,
            'scale': scale
        })

    except Exception as e:
        print(f"❌ Page render error: {e}")
        return jsonify({'success': False, 'error': f'Failed to render page: {str(e)}'})


@app.route('/download_highlighted_pdf')
def download_highlighted_pdf():
    """Download PDF with highlighted mismatches"""
    try:
        if not validator.validation_results:
            return jsonify({'success': False, 'error': 'No validation results available'})

        highlighted_pdf = validator.generate_highlighted_pdf()
        if highlighted_pdf:
            return send_file(
                highlighted_pdf,
                as_attachment=True,
                download_name='validated_pdf_with_highlights.pdf',
                mimetype='application/pdf'
            )
        else:
            return jsonify({'success': False, 'error': 'Failed to generate highlighted PDF'})

    except Exception as e:
        print(f"❌ Download error: {e}")
        return jsonify({'success': False, 'error': f'PDF generation failed: {str(e)}'})


@app.route('/health')
def health_check():
    """Health check"""
    try:
        return jsonify({
            'success': True,
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'memory_usage_mb': validator.get_memory_usage(),
            'config_loaded': validator.config_data is not None,
            'pdf_loaded': validator.pdf_file is not None,
            'scripts_loaded': list(validator.python_scripts.keys())
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


# Debug endpoints
@app.route('/debug/github')
def debug_github():
    """Debug GitHub configuration"""
    return jsonify({
        'GITHUB_TOKEN': 'Set' if GITHUB_TOKEN else 'Not Set',
        'GITHUB_REPO_OWNER': GITHUB_REPO_OWNER,
        'GITHUB_REPO_NAME': GITHUB_REPO_NAME,
        'Expected_URL': f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs'
    })


# Cleanup on exit
def cleanup_on_exit():
    """Cleanup resources on app shutdown"""
    try:
        print("🧹 Cleaning up on app shutdown...")
        validator.cleanup_temp_files()
        if validator.pdf_file:
            validator.pdf_file.close()
        print("✅ Cleanup completed")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")


atexit.register(cleanup_on_exit)

if __name__ == '__main__':
    print("🔍 PDF Validator App Starting...")
    print("📄 Features:")
    print("  ✅ Type-specific field extraction (text, checkbox, signature)")
    print("  ✅ Image-based signature detection")
    print("  ✅ Pre-parsed field access for Script3")
    print("  ✅ Common validation functions")
    print("  ✅ GitHub configuration management")
    print("  ✅ Visual field highlighting")
    print("🌐 http://localhost:5001")

    if GITHUB_TOKEN:
        print(f"✅ GitHub configured: {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
    else:
        print("⚠️ GitHub not configured - set environment variables:")
        print("   GITHUB_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME")

    print("=" * 70)

    try:
        app.run(debug=True, host='0.0.0.0', port=5001, threaded=True)
    except Exception as e:
        print(f"❌ Failed to start server: {e}")
    finally:
        cleanup_on_exit()