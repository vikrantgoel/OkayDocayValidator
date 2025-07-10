from flask import Flask, request, jsonify, render_template
import pdfplumber
import tempfile
import uuid
import json
from datetime import datetime

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# Simplified settings for debugging
PDF_SETTINGS = {
    'base_resolution': 200,  # Lower for testing
    'min_resolution': 150,
}

# Simple session storage for debugging
current_pdf = None
current_pdf_data = {}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/test')
def test():
    """Simple test endpoint"""
    return jsonify({
        'success': True,
        'message': 'Flask app is working!',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    """Simplified PDF upload for debugging"""
    try:
        print("📁 Upload request received")

        # Check if file was uploaded
        if 'pdf_file' not in request.files:
            print("❌ No pdf_file in request")
            return jsonify({'success': False, 'error': 'No file uploaded'})

        file = request.files['pdf_file']
        print(f"📄 File received: {file.filename}")

        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})

        if not file.filename.lower().endswith('.pdf'):
            return jsonify({'success': False, 'error': 'File must be a PDF'})

        # Save PDF to temp file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
            file.save(temp_pdf.name)
            pdf_path = temp_pdf.name
            print(f"💾 PDF saved to: {pdf_path}")

        # Try to open with pdfplumber
        try:
            pdf = pdfplumber.open(pdf_path)
            page_count = len(pdf.pages)
            print(f"📖 PDF opened successfully: {page_count} pages")

            # Store globally for debugging
            global current_pdf, current_pdf_data
            current_pdf = pdf
            current_pdf_data = {
                'filename': file.filename,
                'page_count': page_count,
                'path': pdf_path
            }

            return jsonify({
                'success': True,
                'filename': file.filename,
                'page_count': page_count,
                'message': 'PDF uploaded successfully',
                'debug': True
            })

        except Exception as pdf_error:
            print(f"❌ PDF processing error: {pdf_error}")
            return jsonify({
                'success': False,
                'error': f'PDF processing failed: {str(pdf_error)}'
            })

    except Exception as e:
        print(f"❌ Upload error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'Upload failed: {str(e)}'
        })


@app.route('/get_page/<int:page_num>')
def get_page(page_num):
    """Simplified page rendering for debugging"""
    try:
        print(f"🖼️ Page {page_num} requested")

        if current_pdf is None:
            return jsonify({'success': False, 'error': 'No PDF loaded'})

        if page_num < 1 or page_num > len(current_pdf.pages):
            return jsonify({'success': False, 'error': f'Invalid page number. PDF has {len(current_pdf.pages)} pages.'})

        page = current_pdf.pages[page_num - 1]

        # Simple rendering without enhancements
        scale = float(request.args.get('scale', 1.0))
        resolution = max(150, int(200 * scale))

        print(f"🎨 Rendering at {resolution} DPI")

        img = page.to_image(resolution=resolution, antialias=True)
        pil_img = img.original

        # Convert to base64
        import io
        import base64

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
            'resolution': resolution,
            'debug': True
        })

    except Exception as e:
        print(f"❌ Page render error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/debug_info')
def debug_info():
    """Debug information endpoint"""
    try:
        import sys
        import os

        return jsonify({
            'success': True,
            'python_version': sys.version,
            'working_directory': os.getcwd(),
            'pdf_loaded': current_pdf is not None,
            'pdf_data': current_pdf_data if current_pdf else None,
            'flask_debug': app.debug,
            'available_routes': [str(rule) for rule in app.url_map.iter_rules()]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    print("🔍 Debug Config Generator Starting...")
    print("🌐 Test endpoints:")
    print("   http://localhost:5000/test")
    print("   http://localhost:5000/debug_info")
    print("=" * 50)

    app.run(debug=True, host='0.0.0.0', port=5002)