#!/usr/bin/env python3
"""
Serveur backend pour connecter l'interface web au script publish_private.py
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import subprocess
import os
import tempfile
import threading
import time

app = Flask(__name__)
CORS(app)

# Path to the publish script
PUBLISH_SCRIPT = "/Users/iliaskalalou/TikTok/publish_private.py"

# Store process outputs
process_outputs = {}

@app.route('/api/publish', methods=['POST'])
def publish_video():
    """Execute publish_private.py with the uploaded video"""
    
    if 'video' not in request.files:
        return jsonify({"error": "No video file provided"}), 400
    
    video = request.files['video']
    
    # Save video to temp file
    temp_dir = tempfile.gettempdir()
    video_path = os.path.join(temp_dir, video.filename)
    video.save(video_path)
    
    # Generate a unique ID for this process
    process_id = str(int(time.time() * 1000))
    process_outputs[process_id] = []
    
    def run_publish():
        try:
            # Execute the publish script
            cmd = ['python3', PUBLISH_SCRIPT, video_path, 'private']
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Read output line by line
            for line in iter(process.stdout.readline, ''):
                if line:
                    process_outputs[process_id].append(line.strip())
            
            process.wait()
            
            # Clean up temp file
            if os.path.exists(video_path):
                os.remove(video_path)
                
        except Exception as e:
            process_outputs[process_id].append(f"Error: {str(e)}")
    
    # Run in background
    thread = threading.Thread(target=run_publish)
    thread.start()
    
    return jsonify({"process_id": process_id})

@app.route('/api/status/<process_id>', methods=['GET'])
def get_status(process_id):
    """Get the output of a running process"""
    
    if process_id not in process_outputs:
        return jsonify({"error": "Process not found"}), 404
    
    output = process_outputs[process_id]
    
    # Check if process is complete
    is_complete = any('Processus terminé' in line for line in output)
    
    return jsonify({
        "output": output,
        "is_complete": is_complete
    })

if __name__ == '__main__':
    print("🚀 Backend server started on http://localhost:5001")
    app.run(debug=True, port=5001)