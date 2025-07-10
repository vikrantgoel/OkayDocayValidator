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

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024
app.config['SECRET_KEY'] = 'validator-secret-key-change-in-production'

# GitHub Configuration for downloading configs
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
GITHUB_REPO_OWNER = os.getenv('GITHUB_REPO_OWNER', 'your-username')
GITHUB_REPO_NAME = os.getenv('GITHUB_REPO_NAME', 'pdf-configs')


class PDFValidator:
    def __init__(self):
        self.pdf_file = None
        self.pdf_path = None  # Store PDF file path for highlighting
        self.config_data = None
        self.python_scripts = {}
        self.actual_fields = {}
        self.expected_values = {}
        self.validation_results = {}

    def load_pdf(self, pdf_path):
        """Load PDF file for validation"""
        try:
            self.pdf_file = pdfplumber.open(pdf_path)
            self.pdf_path = pdf_path  # Store path for later use
            print(f"✅ PDF loaded successfully: {len(self.pdf_file.pages)} pages")
            return True
        except Exception as e:
            print(f"❌ PDF loading failed: {str(e)}")
            return False

    def load_config_from_github(self, file_type, config_filename=None):
        """Download specific config and scripts from GitHub"""
        if not GITHUB_TOKEN:
            return {'success': False, 'error': 'GitHub token not configured'}

        try:
            headers = {
                'Authorization': f'token {GITHUB_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }

            if config_filename:
                # Download specific config file
                download_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}/{config_filename}'
                response = requests.get(download_url, headers=headers)

                if response.status_code != 200:
                    return {'success': False, 'error': f'Config file "{config_filename}" not found'}

                file_info = response.json()
                download_response = requests.get(file_info['download_url'])

                if download_response.status_code != 200:
                    return {'success': False, 'error': 'Failed to download config file'}

                # Save and load the specific config
                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                    temp_zip.write(download_response.content)
                    temp_zip_path = temp_zip.name

                return self.load_config_package(temp_zip_path)
            else:
                # Original logic - get latest config
                folder_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}'
                folder_response = requests.get(folder_url, headers=headers)

                if folder_response.status_code != 200:
                    return {'success': False, 'error': f'File type folder "{file_type}" not found'}

                folder_contents = folder_response.json()
                config_files = [item for item in folder_contents if item['name'].endswith('.zip')]

                if not config_files:
                    return {'success': False, 'error': f'No config packages found for file type: {file_type}'}

                # Get the latest config package
                latest_config = sorted(config_files, key=lambda x: x['name'], reverse=True)[0]

                download_response = requests.get(latest_config['download_url'])
                if download_response.status_code != 200:
                    return {'success': False, 'error': 'Failed to download config package'}

                with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                    temp_zip.write(download_response.content)
                    temp_zip_path = temp_zip.name

                return self.load_config_package(temp_zip_path)

        except Exception as e:
            return {'success': False, 'error': f'GitHub download error: {str(e)}'}

    def load_config_package(self, zip_path):
        """Load config JSON and Python scripts from ZIP package"""
        try:
            print(f"📦 Loading config package from: {zip_path}")

            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                file_list = zip_file.namelist()
                print(f"📦 ZIP contains files: {file_list}")

                # Load config JSON - More flexible matching
                config_files = []
                for f in file_list:
                    # Look for JSON files that contain "config" in the name
                    if f.endswith('.json') and ('config' in f.lower() or 'rpa' in f.lower()):
                        config_files.append(f)

                # If no config-named files, try any JSON file
                if not config_files:
                    config_files = [f for f in file_list if f.endswith('.json')]

                print(f"📦 Found config files: {config_files}")

                if config_files:
                    # Use the first JSON file found
                    config_file_name = config_files[0]
                    with zip_file.open(config_file_name) as config_file:
                        config_content = config_file.read().decode('utf-8')
                        self.config_data = json.loads(config_content)
                        print(
                            f"✅ Config loaded from '{config_file_name}': {len(self.config_data.get('pages', {}))} pages")

                        # Debug: Show config structure
                        if 'pages' in self.config_data:
                            for page_num, page_data in self.config_data['pages'].items():
                                fields_count = len(page_data.get('fields', []))
                                print(f"📄 Page {page_num}: {fields_count} fields")
                        else:
                            print("⚠️ Warning: No 'pages' key found in config")
                            print(f"📋 Config keys: {list(self.config_data.keys())}")
                else:
                    print("❌ No JSON files found in package")
                    return {'success': False, 'error': 'No JSON configuration files found in package'}

                # Load Python scripts
                script_files = [f for f in file_list if f.endswith('.py')]
                print(f"📦 Found script files: {script_files}")

                for script_file in script_files:
                    with zip_file.open(script_file) as sf:
                        script_content = sf.read().decode('utf-8')
                        script_name = os.path.basename(script_file).replace('.py', '')
                        self.python_scripts[script_name] = script_content
                        print(f"✅ Script loaded: {script_name}")

            print(f"✅ Package loaded successfully: {len(self.python_scripts)} scripts")
            return {'success': True, 'config': self.config_data, 'scripts': self.python_scripts}

        except json.JSONDecodeError as e:
            print(f"❌ JSON parsing error: {str(e)}")
            return {'success': False, 'error': f'Invalid JSON in configuration file: {str(e)}'}
        except Exception as e:
            print(f"❌ Failed to load config package: {str(e)}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': f'Failed to load config package: {str(e)}'}

    def extract_actual_fields(self):
        """Extract actual field values from PDF using config"""
        print(f"🔍 Debug: PDF loaded: {self.pdf_file is not None}")
        print(f"🔍 Debug: Config loaded: {self.config_data is not None}")

        if not self.pdf_file:
            return {'success': False, 'error': 'PDF not loaded - please upload a PDF file first'}

        if not self.config_data:
            return {'success': False, 'error': 'Configuration not loaded - please check GitHub repository access'}

        try:
            self.actual_fields = {}
            total_fields_found = 0

            print(f"🔍 Debug: Config pages: {list(self.config_data.get('pages', {}).keys())}")

            for page_num, page_data in self.config_data.get('pages', {}).items():
                page_index = int(page_num) - 1

                print(f"🔍 Debug: Processing page {page_num} (index {page_index})")
                print(f"🔍 Debug: PDF has {len(self.pdf_file.pages)} pages")

                if page_index >= len(self.pdf_file.pages):
                    print(f"⚠️  Warning: Page {page_num} not found in PDF (only {len(self.pdf_file.pages)} pages)")
                    continue

                page = self.pdf_file.pages[page_index]
                self.actual_fields[page_num] = {}

                fields_in_page = page_data.get('fields', [])
                print(f"🔍 Debug: Page {page_num} has {len(fields_in_page)} fields to extract")

                for field in fields_in_page:
                    field_name = field['name']
                    field_type = field['type']
                    coordinates = field['coordinates']

                    print(f"🔍 Debug: Extracting field '{field_name}' ({field_type}) at {coordinates}")

                    # Parse coordinates
                    try:
                        coords = [float(c.strip()) for c in coordinates.split(',')]
                        if len(coords) != 4:
                            print(f"❌ Invalid coordinates for field '{field_name}': {coordinates}")
                            continue
                    except ValueError as e:
                        print(f"❌ Error parsing coordinates for field '{field_name}': {e}")
                        continue

                    x1, y1, x2, y2 = coords

                    try:
                        if field_type == 'text':
                            # Extract text from coordinates
                            cropped_page = page.crop((x1, y1, x2, y2))
                            actual_value = cropped_page.extract_text().strip()

                        elif field_type == 'checkbox':
                            # Check if checkbox is marked (look for 'X' or similar marks)
                            cropped_page = page.crop((x1, y1, x2, y2))
                            text_in_area = cropped_page.extract_text().strip()
                            # Simple checkbox detection
                            actual_value = 'checked' if (
                                        'X' in text_in_area or '✓' in text_in_area or '☑' in text_in_area) else 'unchecked'

                        elif field_type == 'signature':
                            # Check if signature area has content (not blank)
                            cropped_page = page.crop((x1, y1, x2, y2))
                            text_in_area = cropped_page.extract_text().strip()
                            words = cropped_page.extract_words()
                            actual_value = 'signed' if (text_in_area or len(words) > 0) else 'blank'

                        else:
                            actual_value = ''

                        self.actual_fields[page_num][field_name] = {
                            'type': field_type,
                            'value': actual_value,
                            'coordinates': coordinates
                        }

                        total_fields_found += 1
                        print(f"✅ Extracted '{field_name}': '{actual_value}'")

                    except Exception as e:
                        print(f"❌ Error extracting field '{field_name}': {str(e)}")
                        continue

            print(f"🎯 Total fields extracted: {total_fields_found}")
            return {'success': True, 'actual_fields': self.actual_fields, 'total_extracted': total_fields_found}

        except Exception as e:
            print(f"❌ Field extraction failed: {str(e)}")
            return {'success': False, 'error': f'Field extraction failed: {str(e)}'}

    def generate_expected_values(self, input_data):
        """Generate expected values using python_code2 (script2)"""
        if 'script2' not in self.python_scripts:
            # Fallback: directly use input_data as expected values
            self.expected_values = input_data
            return {'success': True, 'expected_values': self.expected_values}

        try:
            # Execute script2.py to generate expected values
            script2_code = self.python_scripts['script2']

            # Create a safe execution environment
            exec_globals = {
                'input_data': input_data,
                'config': self.config_data,
                'json': json,
                'expected_values': {}
            }

            # Execute the script
            exec(script2_code, exec_globals)

            # Get the expected values from the execution
            if 'expected_values' in exec_globals:
                self.expected_values = exec_globals['expected_values']
            else:
                self.expected_values = input_data

            return {'success': True, 'expected_values': self.expected_values}

        except Exception as e:
            # Fallback to input data if script fails
            self.expected_values = input_data
            return {'success': True, 'expected_values': self.expected_values, 'warning': f'Script2 error: {str(e)}'}

    def validate_fields(self):
        """Validate expected vs actual values using python_code3 (script3)"""
        if not self.expected_values or not self.actual_fields:
            return {'success': False, 'error': 'Expected or actual values not available'}

        try:
            if 'script3' in self.python_scripts:
                # Use script3 for validation logic
                script3_code = self.python_scripts['script3']

                exec_globals = {
                    'expected_values': self.expected_values,
                    'actual_fields': self.actual_fields,
                    'config': self.config_data,
                    'json': json,
                    'validation_results': {}
                }

                exec(script3_code, exec_globals)

                if 'validation_results' in exec_globals:
                    self.validation_results = exec_globals['validation_results']
                else:
                    # Fallback validation
                    self.validation_results = self._default_validation()
            else:
                # Default validation logic
                self.validation_results = self._default_validation()

            return {'success': True, 'validation_results': self.validation_results}

        except Exception as e:
            # Fallback to default validation if script fails
            self.validation_results = self._default_validation()
            return {'success': True, 'validation_results': self.validation_results,
                    'warning': f'Script3 error: {str(e)}'}

    def _default_validation(self):
        """Default validation logic when script3 is not available"""
        results = {
            'mismatches': [],
            'matches': [],
            'summary': {}
        }

        total_fields = 0
        matching_fields = 0

        for page_num, page_fields in self.actual_fields.items():
            for field_name, field_data in page_fields.items():
                total_fields += 1
                actual_value = field_data['value']
                expected_value = self.expected_values.get(field_name, '')

                # Field-type specific validation
                field_type = field_data['type']

                if field_type == 'text':
                    # Text fields: exact match or contains
                    is_match = (actual_value.lower() == str(expected_value).lower()) or (
                                str(expected_value).lower() in actual_value.lower())
                elif field_type == 'checkbox':
                    # Checkbox: expected should be 'checked' or 'unchecked'
                    is_match = actual_value == expected_value
                elif field_type == 'signature':
                    # Signature: expected should be 'signed' or 'blank'
                    is_match = actual_value == expected_value
                else:
                    is_match = str(actual_value) == str(expected_value)

                result_entry = {
                    'field_name': field_name,
                    'field_type': field_type,
                    'page': page_num,
                    'coordinates': field_data['coordinates'],
                    'expected': expected_value,
                    'actual': actual_value,
                    'match': is_match
                }

                if is_match:
                    results['matches'].append(result_entry)
                    matching_fields += 1
                else:
                    results['mismatches'].append(result_entry)

        results['summary'] = {
            'total_fields': total_fields,
            'matching_fields': matching_fields,
            'mismatched_fields': len(results['mismatches']),
            'accuracy_percentage': round((matching_fields / total_fields * 100), 2) if total_fields > 0 else 0
        }

        return results

    def generate_highlighted_pdf(self):
        """Generate PDF with highlighted mismatched fields"""
        if not self.validation_results or not self.pdf_path:
            print("❌ Cannot generate highlighted PDF: missing validation results or PDF path")
            return None

        try:
            print(f"🎨 Generating highlighted PDF from: {self.pdf_path}")

            # Create a copy of the PDF for highlighting
            pdf_bytes = io.BytesIO()

            # Use PyMuPDF for highlighting
            pdf_doc = fitz.open(self.pdf_path)

            mismatches = self.validation_results.get('mismatches', [])
            print(f"🎨 Highlighting {len(mismatches)} mismatched fields")

            for mismatch in mismatches:
                page_num = int(mismatch['page']) - 1
                coordinates = mismatch['coordinates']
                coords = [float(c.strip()) for c in coordinates.split(',')]

                if len(coords) == 4 and page_num < len(pdf_doc):
                    x1, y1, x2, y2 = coords
                    page = pdf_doc[page_num]

                    # Create highlight rectangle
                    rect = fitz.Rect(x1, y1, x2, y2)

                    # Add highlight annotation
                    highlight = page.add_highlight_annot(rect)
                    highlight.set_colors(stroke=[1, 0, 0])  # Red highlight
                    highlight.update()

                    print(f"🎨 Highlighted field '{mismatch['field_name']}' on page {page_num + 1}")

            # Save the highlighted PDF
            pdf_doc.save(pdf_bytes)
            pdf_doc.close()

            pdf_bytes.seek(0)
            print("✅ Highlighted PDF generated successfully")
            return pdf_bytes

        except Exception as e:
            print(f"❌ PDF highlighting error: {e}")
            import traceback
            traceback.print_exc()
            return None


# Global validator instance
validator = PDFValidator()


@app.route('/')
def index():
    return render_template('validator.html')


@app.route('/get_available_file_types')
def get_available_file_types():
    """Get list of available file types from GitHub repository"""
    if not GITHUB_TOKEN:
        return jsonify({
            'success': False,
            'error': 'GitHub token not configured',
            'file_types': []
        })

    try:
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json'
        }

        # Get contents of configs folder
        url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs'
        response = requests.get(url, headers=headers)

        if response.status_code == 404:
            return jsonify({
                'success': True,
                'file_types': [],
                'message': 'No configs folder found in repository'
            })
        elif response.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Failed to access repository: {response.status_code}',
                'file_types': []
            })

        contents = response.json()

        # Extract folder names (file types)
        file_types = []
        for item in contents:
            if item['type'] == 'dir':
                folder_name = item['name']

                # Check if folder contains config files
                folder_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{folder_name}'
                folder_response = requests.get(folder_url, headers=headers)

                if folder_response.status_code == 200:
                    folder_contents = folder_response.json()
                    # Get all ZIP files (config packages)
                    zip_files = [f for f in folder_contents if f['type'] == 'file' and f['name'].endswith('.zip')]

                    if zip_files:
                        # Sort ZIP files by name (newest first, assuming timestamp in name)
                        zip_files.sort(key=lambda x: x['name'], reverse=True)

                        configs = []
                        for zip_file in zip_files:
                            # Extract timestamp and description from filename
                            filename = zip_file['name'].replace('.zip', '')
                            parts = filename.split('_')

                            # Try to parse timestamp and type
                            if len(parts) >= 3 and len(parts[0]) >= 10:  # Has timestamp
                                timestamp = parts[0] + '_' + parts[1]  # YYYY-MM-DD_HH-MM-SS
                                config_type = '_'.join(parts[2:])  # Everything after timestamp
                                display_name = f"{timestamp} ({config_type})"
                            else:
                                display_name = filename
                                timestamp = 'Unknown'
                                config_type = filename

                            configs.append({
                                'filename': zip_file['name'],
                                'display_name': display_name,
                                'download_url': zip_file['download_url'],
                                'size': zip_file['size'],
                                'timestamp': timestamp,
                                'type': config_type
                            })

                        file_types.append({
                            'name': folder_name,
                            'display_name': folder_name.replace('_', ' ').title(),
                            'config_count': len(configs),
                            'configs': configs,
                            'latest_config': configs[0] if configs else None
                        })

        # Sort by display name
        file_types.sort(key=lambda x: x['display_name'])

        return jsonify({
            'success': True,
            'file_types': file_types,
            'total_types': len(file_types)
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error fetching file types: {str(e)}',
            'file_types': []
        })


@app.route('/get_config_versions/<file_type>')
def get_config_versions(file_type):
    """Get all available configuration versions for a specific file type"""
    if not GITHUB_TOKEN:
        return jsonify({'success': False, 'error': 'GitHub token not configured'})

    try:
        headers = {
            'Authorization': f'token {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json'
        }

        # Get contents of specific file type folder
        folder_url = f'https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/contents/configs/{file_type}'
        response = requests.get(folder_url, headers=headers)

        if response.status_code != 200:
            return jsonify({'success': False, 'error': f'File type "{file_type}" not found'})

        folder_contents = response.json()
        zip_files = [f for f in folder_contents if f['type'] == 'file' and f['name'].endswith('.zip')]

        if not zip_files:
            return jsonify({'success': False, 'error': f'No configurations found for "{file_type}"'})

        # Sort by name (newest first)
        zip_files.sort(key=lambda x: x['name'], reverse=True)

        configs = []
        for zip_file in zip_files:
            filename = zip_file['name'].replace('.zip', '')
            parts = filename.split('_')

            # Parse filename for better display
            if len(parts) >= 3 and len(parts[0]) >= 10:
                date_part = parts[0]  # YYYY-MM-DD
                time_part = parts[1]  # HH-MM-SS
                config_type = '_'.join(parts[2:])

                # Format display name
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

        return jsonify({
            'success': True,
            'file_type': file_type,
            'configs': configs,
            'total_configs': len(configs)
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'Error fetching config versions: {str(e)}'})


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    """Upload PDF file for validation"""
    try:
        if 'pdf_file' not in request.files:
            return jsonify({'success': False, 'error': 'No PDF file uploaded'})

        pdf_file = request.files['pdf_file']
        file_type = request.form.get('file_type', '').strip()
        config_filename = request.form.get('config_filename', '').strip()  # Optional specific config

        print(f"🔍 Debug: Received file_type: '{file_type}'")
        print(f"🔍 Debug: Received config_filename: '{config_filename}'")

        if not file_type:
            return jsonify({'success': False, 'error': 'Please specify the file type'})

        if not pdf_file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'File must be a PDF'})

        # Save uploaded PDF
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
            pdf_file.save(temp_pdf.name)
            pdf_path = temp_pdf.name

        print(f"🔍 Debug: PDF saved to: {pdf_path}")

        # Load PDF
        pdf_loaded = validator.load_pdf(pdf_path)
        if not pdf_loaded:
            return jsonify({'success': False, 'error': 'Failed to load PDF file'})

        # Download specific config from GitHub
        print(f"🔍 Debug: Loading config from GitHub...")
        if config_filename:
            config_result = validator.load_config_from_github(file_type, config_filename)
        else:
            config_result = validator.load_config_from_github(file_type)

        print(f"🔍 Debug: Config result: {config_result.get('success', False)}")
        if not config_result['success']:
            return jsonify(config_result)

        # Extract actual fields
        print(f"🔍 Debug: Extracting actual fields...")
        extraction_result = validator.extract_actual_fields()
        print(f"🔍 Debug: Extraction result: {extraction_result.get('success', False)}")

        if not extraction_result['success']:
            return jsonify(extraction_result)

        total_fields = extraction_result.get('total_extracted', 0)

        return jsonify({
            'success': True,
            'message': f'PDF and configuration "{config_filename or "latest"}" loaded successfully',
            'config': validator.config_data,
            'actual_fields': validator.actual_fields,
            'scripts_available': list(validator.python_scripts.keys()),
            'config_info': {
                'file_type': file_type,
                'config_filename': config_filename or 'latest',
                'total_fields': total_fields
            },
            'debug_info': {
                'pdf_pages': len(validator.pdf_file.pages) if validator.pdf_file else 0,
                'config_pages': len(validator.config_data.get('pages', {})) if validator.config_data else 0,
                'scripts_loaded': len(validator.python_scripts),
                'fields_extracted': total_fields
            }
        })

    except Exception as e:
        print(f"❌ Upload error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Upload failed: {str(e)}'})


@app.route('/generate_input_form', methods=['GET'])
def generate_input_form():
    """Generate HTML input form based on config using script1"""
    try:
        if not validator.config_data:
            return jsonify({'success': False, 'error': 'No configuration loaded'})

        if 'script1' in validator.python_scripts:
            # Use script1 to generate form
            script1_code = validator.python_scripts['script1']

            exec_globals = {
                'config': validator.config_data,
                'json': json,
                'form_html': ''
            }

            exec(script1_code, exec_globals)

            if 'form_html' in exec_globals and exec_globals['form_html']:
                form_html = exec_globals['form_html']
            else:
                form_html = _generate_default_form()
        else:
            # Generate default form
            form_html = _generate_default_form()

        return jsonify({'success': True, 'form_html': form_html})

    except Exception as e:
        # Fallback to default form
        form_html = _generate_default_form()
        return jsonify({'success': True, 'form_html': form_html, 'warning': f'Script1 error: {str(e)}'})


def _generate_default_form():
    """Generate default HTML form when script1 is not available"""
    if not validator.config_data:
        return '<p>No configuration available</p>'

    form_html = '<form id="validation-form" class="validation-form">'

    for page_num, page_data in validator.config_data.get('pages', {}).items():
        form_html += f'<div class="page-section"><h3>Page {page_num}</h3>'

        for field in page_data.get('fields', []):
            field_name = field['name']
            field_type = field['type']

            form_html += f'<div class="form-group">'
            form_html += f'<label for="{field_name}">{field_name.replace("_", " ").title()} ({field_type})</label>'

            if field_type == 'text':
                form_html += f'<input type="text" id="{field_name}" name="{field_name}" class="form-control">'
            elif field_type == 'checkbox':
                form_html += f'''
                    <select id="{field_name}" name="{field_name}" class="form-control">
                        <option value="unchecked">Unchecked</option>
                        <option value="checked">Checked</option>
                    </select>
                '''
            elif field_type == 'signature':
                form_html += f'''
                    <select id="{field_name}" name="{field_name}" class="form-control">
                        <option value="blank">Blank</option>
                        <option value="signed">Signed</option>
                    </select>
                '''
            else:
                form_html += f'<input type="text" id="{field_name}" name="{field_name}" class="form-control">'

            form_html += '</div>'

        form_html += '</div>'

    form_html += '<button type="submit" class="btn btn-primary">Validate PDF</button>'
    form_html += '</form>'

    return form_html


@app.route('/validate_pdf', methods=['POST'])
def validate_pdf():
    """Validate PDF using input form data"""
    try:
        input_data = request.get_json()

        if not validator.config_data or not validator.actual_fields:
            return jsonify({'success': False, 'error': 'PDF not loaded or fields not extracted'})

        # Generate expected values using script2
        expected_result = validator.generate_expected_values(input_data)
        if not expected_result['success']:
            return jsonify(expected_result)

        # Validate fields using script3
        validation_result = validator.validate_fields()
        if not validation_result['success']:
            return jsonify(validation_result)

        return jsonify({
            'success': True,
            'validation_results': validator.validation_results,
            'expected_values': validator.expected_values,
            'actual_fields': validator.actual_fields
        })

    except Exception as e:
        return jsonify({'success': False, 'error': f'Validation failed: {str(e)}'})


@app.route('/get_page/<int:page_num>')
def get_page(page_num):
    """Render PDF page for visual field location"""
    try:
        print(f"🖼️ Rendering page {page_num} for visual field location")

        if not validator.pdf_file:
            print("❌ No PDF loaded")
            return jsonify({'success': False, 'error': 'No PDF loaded'})

        scale = float(request.args.get('scale', 1.0))

        if page_num < 1 or page_num > len(validator.pdf_file.pages):
            print(f"❌ Invalid page number {page_num} (PDF has {len(validator.pdf_file.pages)} pages)")
            return jsonify(
                {'success': False, 'error': f'Invalid page number. PDF has {len(validator.pdf_file.pages)} pages.'})

        page = validator.pdf_file.pages[page_num - 1]
        print(f"✅ Loading page {page_num} with scale {scale}")

        # Convert PDF page to image
        base_resolution = 150
        resolution = max(100, int(base_resolution * scale))

        # Use pdfplumber's to_image method
        img = page.to_image(resolution=resolution, antialias=True)
        pil_img = img.original

        # Convert to base64
        img_buffer = io.BytesIO()
        pil_img.save(img_buffer, format='PNG', optimize=True, compress_level=6)
        img_buffer.seek(0)
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

        print(f"✅ Page {page_num} rendered successfully: {pil_img.width}x{pil_img.height}")

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
        print(f"❌ Failed to render page {page_num}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Failed to render page: {str(e)}'})


@app.route('/download_highlighted_pdf')
def download_highlighted_pdf():
    """Download PDF with highlighted mismatched fields"""
    try:
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
        return jsonify({'success': False, 'error': f'PDF generation failed: {str(e)}'})


@app.route('/get_validation_summary')
def get_validation_summary():
    """Get validation summary and results"""
    if not validator.validation_results:
        return jsonify({'success': False, 'error': 'No validation results available'})

    return jsonify({
        'success': True,
        'validation_results': validator.validation_results,
        'config_loaded': validator.config_data is not None,
        'scripts_loaded': list(validator.python_scripts.keys())
    })


if __name__ == '__main__':
    print("🔍 PDF Validator App Starting...")
    print("📄 Features:")
    print("  - Download configs from GitHub")
    print("  - Extract actual field values from PDF")
    print("  - Generate input forms using script1")
    print("  - Generate expected values using script2")
    print("  - Validate using custom logic in script3")
    print("  - Highlight mismatched fields on PDF")
    print("  - Visual field location with clickable links")
    print("🌐 http://localhost:5001")
    print("=" * 50)

    if GITHUB_TOKEN:
        print(f"✅ GitHub configured: {GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}")
    else:
        print("⚠️  GitHub not configured - set environment variables:")
        print("   GITHUB_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME")

    print("=" * 50)

    app.run(debug=True, host='0.0.0.0', port=5001)